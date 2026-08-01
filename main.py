from flask import Flask, request, jsonify
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import binascii
import aiohttp
import requests
import json
import like_pb2
import like_count_pb2
import uid_generator_pb2
import threading
import urllib3
import random
import time

# Configuration
TOKEN_BATCH_SIZE = 220
RETRY_ATTEMPTS = 1  # Number of retry attempts for failed requests
PROFILE_RETRY_ATTEMPTS = 1  # Increased retry attempts for profile check
PROFILE_RETRY_DELAY = 0.1  # Increased delay to 0.5 seconds
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Global State for Batch Management
current_batch_indices = {}
batch_indices_lock = threading.Lock()

def get_next_batch_tokens(server_name, all_tokens):
    if not all_tokens:
        return []
    
    total_tokens = len(all_tokens)
    
    # If we have fewer tokens than batch size, use all available tokens
    if total_tokens <= TOKEN_BATCH_SIZE:
        return all_tokens
    
    with batch_indices_lock:
        if server_name not in current_batch_indices:
            current_batch_indices[server_name] = 0
        
        current_index = current_batch_indices[server_name]
        
        # Calculate the batch
        start_index = current_index
        end_index = start_index + TOKEN_BATCH_SIZE
        
        # If we reach or exceed the end, wrap around
        if end_index > total_tokens:
            remaining = end_index - total_tokens
            batch_tokens = all_tokens[start_index:total_tokens] + all_tokens[0:remaining]
        else:
            batch_tokens = all_tokens[start_index:end_index]
        
        # Update the index for next time
        next_index = (current_index + TOKEN_BATCH_SIZE) % total_tokens
        current_batch_indices[server_name] = next_index
        
        return batch_tokens

def get_random_batch_tokens(server_name, all_tokens):
    """Alternative method: use random sampling for better distribution"""
    if not all_tokens:
        return []
    
    total_tokens = len(all_tokens)
    
    # If we have fewer tokens than batch size, use all available tokens
    if total_tokens <= TOKEN_BATCH_SIZE:
        return all_tokens.copy()
    
    # Randomly select tokens without replacement
    return random.sample(all_tokens, TOKEN_BATCH_SIZE)

def load_tokens(server_name, for_visit=False):
    if for_visit:
        if server_name == "IND":
            path = "token_ind_visit.json"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            path = "token_br_visit.json"
        else:
            path = "token_bd_visit.json"
    else:
        if server_name == "IND":
            path = "token_ind.json"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            path = "token_br.json"
        else:
            path = "token_bd.json"

    try:
        with open(path, "r") as f:
            tokens = json.load(f)
            if isinstance(tokens, list) and all(isinstance(t, dict) and "token" in t for t in tokens):
                print(f"Loaded {len(tokens)} tokens from {path} for server {server_name}")
                return tokens
            else:
                print(f"Warning: Token file {path} is not in the expected format. Returning empty list.")
                return []
    except FileNotFoundError:
        print(f"Warning: Token file {path} not found. Returning empty list for server {server_name}.")
        return []
    except json.JSONDecodeError:
        print(f"Warning: Token file {path} contains invalid JSON. Returning empty list.")
        return []

def encrypt_message(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded_message = pad(plaintext, AES.block_size)
    encrypted_message = cipher.encrypt(padded_message)
    return binascii.hexlify(encrypted_message).decode('utf-8')

def create_protobuf_message(user_id, region):
    message = like_pb2.like()
    message.uid = int(user_id)
    message.region = region
    return message.SerializeToString()

def create_protobuf_for_profile_check(uid):
    message = uid_generator_pb2.uid_generator()
    message.krishna_ = int(uid)
    message.teamXdarks = 1
    return message.SerializeToString()

def enc_profile_check_payload(uid):
    protobuf_data = create_protobuf_for_profile_check(uid)
    encrypted_uid = encrypt_message(protobuf_data)
    return encrypted_uid

async def send_single_like_request(encrypted_like_payload, token_dict, url, retry_count=0):
    edata = bytes.fromhex(encrypted_like_payload)
    token_value = token_dict.get("token", "")
    if not token_value:
        print("Warning: send_single_like_request received an empty or invalid token_dict.")
        return 999

    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token_value}",
        'Content-Type': "application/x-www-form-urlencoded",
        'Expect': "100-continue",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB54"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200 and retry_count < RETRY_ATTEMPTS:
                    # Retry once if failed
                    print(f"Retrying failed request for token {token_value[:10]}... (Attempt {retry_count + 1})")
                    await asyncio.sleep(0.5)  # Small delay before retry
                    return await send_single_like_request(encrypted_like_payload, token_dict, url, retry_count + 1)
                return response.status
    except asyncio.TimeoutError:
        if retry_count < RETRY_ATTEMPTS:
            print(f"Timeout, retrying for token {token_value[:10]}... (Attempt {retry_count + 1})")
            await asyncio.sleep(0.5)
            return await send_single_like_request(encrypted_like_payload, token_dict, url, retry_count + 1)
        print(f"Like request timed out for token {token_value[:10]}... after retry")
        return 998
    except Exception as e:
        if retry_count < RETRY_ATTEMPTS:
            print(f"Exception, retrying for token {token_value[:10]}... (Attempt {retry_count + 1})")
            await asyncio.sleep(0.5)
            return await send_single_like_request(encrypted_like_payload, token_dict, url, retry_count + 1)
        print(f"Exception in send_single_like_request for token {token_value[:10]}... after retry: {e}")
        return 997

async def send_likes_with_token_batch(uid, server_region_for_like_proto, like_api_url, token_batch_to_use):
    if not token_batch_to_use:
        print("No tokens provided in the batch to send_likes_with_token_batch.")
        return []

    like_protobuf_payload = create_protobuf_message(uid, server_region_for_like_proto)
    encrypted_like_payload = encrypt_message(like_protobuf_payload)
    
    tasks = []
    for token_dict_for_request in token_batch_to_use:
        tasks.append(send_single_like_request(encrypted_like_payload, token_dict_for_request, like_api_url))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    successful_sends = sum(1 for r in results if isinstance(r, int) and r == 200)
    failed_sends = len(token_batch_to_use) - successful_sends
    print(f"Attempted {len(token_batch_to_use)} like sends from batch. Successful: {successful_sends}, Failed/Error: {failed_sends}")
    return results

def make_profile_check_request(encrypted_profile_payload, server_name, token_dict, retry_count=0):
    token_value = token_dict.get("token", "")
    if not token_value:
        print("Warning: make_profile_check_request received an empty token_dict.")
        return None

    if server_name == "IND":
        url = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        url = "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    else:
        url = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"

    edata = bytes.fromhex(encrypted_profile_payload)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token_value}",
        'Content-Type': "application/x-www-form-urlencoded",
        'Expect': "100-continue",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB54"
    }
    try:
        response = requests.post(url, data=edata, headers=headers, verify=False, timeout=10)
        
        # Handle 429 Too Many Requests specially
        if response.status_code == 429:
            if retry_count < PROFILE_RETRY_ATTEMPTS:
                wait_time = PROFILE_RETRY_DELAY * (retry_count + 1)  # Exponential backoff
                print(f"Rate limited (429), retrying profile check after {wait_time}s (Attempt {retry_count + 1})")
                time.sleep(wait_time)
                return make_profile_check_request(encrypted_profile_payload, server_name, token_dict, retry_count + 1)
            else:
                print(f"Rate limited (429) after {PROFILE_RETRY_ATTEMPTS} retries for token {token_value[:10]}...")
                return None
        
        response.raise_for_status()
        binary_data = response.content
        decoded_info = decode_protobuf_profile_info(binary_data)
        return decoded_info
    except requests.exceptions.HTTPError as e:
        if retry_count < PROFILE_RETRY_ATTEMPTS:
            print(f"HTTP error, retrying profile check for token {token_value[:10]}... (Attempt {retry_count + 1}): {e.response.status_code}")
            time.sleep(PROFILE_RETRY_DELAY)
            return make_profile_check_request(encrypted_profile_payload, server_name, token_dict, retry_count + 1)
        print(f"HTTP error in make_profile_check_request for token {token_value[:10]}...: {e.response.status_code} - {e.response.text[:100]}")
    except requests.exceptions.RequestException as e:
        if retry_count < PROFILE_RETRY_ATTEMPTS:
            print(f"Request error, retrying profile check for token {token_value[:10]}... (Attempt {retry_count + 1}): {e}")
            time.sleep(PROFILE_RETRY_DELAY)
            return make_profile_check_request(encrypted_profile_payload, server_name, token_dict, retry_count + 1)
        print(f"Request error in make_profile_check_request for token {token_value[:10]}...: {e}")
    except Exception as e:
        if retry_count < PROFILE_RETRY_ATTEMPTS:
            print(f"Unexpected error, retrying profile check for token {token_value[:10]}... (Attempt {retry_count + 1}): {e}")
            time.sleep(PROFILE_RETRY_DELAY)
            return make_profile_check_request(encrypted_profile_payload, server_name, token_dict, retry_count + 1)
        print(f"Unexpected error in make_profile_check_request for token {token_value[:10]}... processing response: {e}")
    return None

def decode_protobuf_profile_info(binary_data):
    try:
        items = like_count_pb2.Info()
        items.ParseFromString(binary_data)
        return items
    except Exception as e:
        print(f"Error decoding Protobuf profile data: {e}")
        return None

app = Flask(__name__)

@app.route('/like', methods=['GET'])
def handle_requests():
    uid_param = request.args.get("uid")
    server_name_param = request.args.get("server_name", "").upper()
    use_random = request.args.get("random", "false").lower() == "true"
    use_random_visit = request.args.get("random_visit", "true").lower() == "true"  # Default random visit tokens

    if not uid_param or not server_name_param:
        return jsonify({"error": "UID and server_name are required"}), 400

    # Load visit token for profile checking
    visit_tokens = load_tokens(server_name_param, for_visit=True)
    if not visit_tokens:
        return jsonify({"error": f"No visit tokens loaded for server {server_name_param}."}), 500
    
    # Randomly shuffle visit tokens if random_visit is enabled
    if use_random_visit and len(visit_tokens) > 1:
        shuffled_visit_tokens = visit_tokens.copy()
        random.shuffle(shuffled_visit_tokens)
        print(f"Using RANDOM visit tokens for {server_name_param} (total {len(shuffled_visit_tokens)} tokens)")
        visit_tokens_to_try = shuffled_visit_tokens[:5]  # Try first 5 random tokens
    else:
        print(f"Using SEQUENTIAL visit tokens for {server_name_param}")
        visit_tokens_to_try = visit_tokens[:5]  # Try first 5 sequential tokens
    
    # Load regular tokens for like sending
    all_available_tokens = load_tokens(server_name_param, for_visit=False)
    if not all_available_tokens:
        return jsonify({"error": f"No tokens loaded or token file invalid for server {server_name_param}."}), 500

    print(f"Total tokens available for {server_name_param}: {len(all_available_tokens)}")

    # Get the batch of tokens for like sending
    if use_random:
        tokens_for_like_sending = get_random_batch_tokens(server_name_param, all_available_tokens)
        print(f"Using RANDOM batch selection for {server_name_param}")
    else:
        tokens_for_like_sending = get_next_batch_tokens(server_name_param, all_available_tokens)
        print(f"Using ROTATING batch selection for {server_name_param}")
    
    encrypted_player_uid_for_profile = enc_profile_check_payload(uid_param)
    
    # Get likes BEFORE using visit token (with retry and random tokens)
    before_info = None
    before_like_count = 0
    working_visit_token = None

    # Try multiple random visit tokens
    for idx, token in enumerate(visit_tokens_to_try):
        print(f"Attempting profile check with visit token {idx + 1} (token: {token.get('token', '')[:10]}...)")
        before_info = make_profile_check_request(encrypted_player_uid_for_profile, server_name_param, token)
        if before_info and hasattr(before_info, 'AccountInfo'):
            before_like_count = int(before_info.AccountInfo.Likes)
            working_visit_token = token  # Use this token for after check too
            print(f"✓ Success with visit token {idx + 1}")
            break
        else:
            print(f"✗ Visit token {idx + 1} failed, trying next...")
            time.sleep(0.2)
    
    if not before_info:
        print(f"Could not reliably fetch 'before' profile info for UID {uid_param} on {server_name_param}.")
        # Use first token as fallback
        working_visit_token = visit_tokens[0] if visit_tokens else None

    print(f"UID {uid_param} ({server_name_param}): Likes before = {before_like_count}")

    # Determine the URL for sending likes
    if server_name_param == "IND":
        like_api_url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server_name_param in {"BR", "US", "SAC", "NA"}:
        like_api_url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        like_api_url = "https://clientbp.ggpolarbear.com/LikeProfile"

    if tokens_for_like_sending:
        print(f"Using token batch for {server_name_param} (size {len(tokens_for_like_sending)}) to send likes.")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(send_likes_with_token_batch(uid_param, server_name_param, like_api_url, tokens_for_like_sending))
        finally:
            loop.close()
    else:
        print(f"Skipping like sending for UID {uid_param} as no tokens available for like sending.")
        
    # Get likes AFTER using the working visit token
    after_info = None
    after_like_count = before_like_count
    actual_player_uid_from_profile = int(uid_param)
    player_nickname_from_profile = "N/A"

    if working_visit_token:
        after_info = make_profile_check_request(encrypted_player_uid_for_profile, server_name_param, working_visit_token)

    if after_info and hasattr(after_info, 'AccountInfo'):
        after_like_count = int(after_info.AccountInfo.Likes)
        actual_player_uid_from_profile = int(after_info.AccountInfo.UID)
        if after_info.AccountInfo.PlayerNickname:
            player_nickname_from_profile = str(after_info.AccountInfo.PlayerNickname)
        else:
            player_nickname_from_profile = "N/A"
    else:
        print(f"Could not reliably fetch 'after' profile info for UID {uid_param} on {server_name_param}.")

    print(f"UID {uid_param} ({server_name_param}): Likes after = {after_like_count}")

    likes_increment = after_like_count - before_like_count
    request_status = 1 if likes_increment > 0 else (2 if likes_increment == 0 else 3)

    visit_mode = "RANDOM" if use_random_visit else "SEQUENTIAL"
    response_data = {
        "LikesGivenByAPI": likes_increment,
        "LikesafterCommand": after_like_count,
        "LikesbeforeCommand": before_like_count,
        "PlayerNickname": player_nickname_from_profile,
        "UID": actual_player_uid_from_profile,
        "status": request_status,
        "Note": f"Used {visit_mode} visit tokens for profile check (tried {len(visit_tokens_to_try)} tokens with {PROFILE_RETRY_ATTEMPTS} retry each, {PROFILE_RETRY_DELAY}s delay) and {'random' if use_random else 'rotating'} batch of {len(tokens_for_like_sending)} tokens for like sending."
    }
    return jsonify(response_data)

@app.route('/profile_info', methods=['GET'])
def get_profile_info():
    """Separate endpoint to get profile information only"""
    uid_param = request.args.get("uid")
    server_name_param = request.args.get("server_name", "").upper()
    use_random_visit = request.args.get("random_visit", "true").lower() == "true"

    if not uid_param or not server_name_param:
        return jsonify({"error": "UID and server_name are required"}), 400

    # Load visit tokens
    visit_tokens = load_tokens(server_name_param, for_visit=True)
    if not visit_tokens:
        return jsonify({"error": f"No visit tokens loaded for server {server_name_param}."}), 500
    
    # Randomly shuffle visit tokens if random_visit is enabled
    if use_random_visit and len(visit_tokens) > 1:
        shuffled_visit_tokens = visit_tokens.copy()
        random.shuffle(shuffled_visit_tokens)
        print(f"Using RANDOM visit tokens for profile info {server_name_param}")
        visit_tokens_to_try = shuffled_visit_tokens[:5]
    else:
        print(f"Using SEQUENTIAL visit tokens for profile info {server_name_param}")
        visit_tokens_to_try = visit_tokens[:5]
    
    encrypted_player_uid_for_profile = enc_profile_check_payload(uid_param)
    
    # Try multiple visit tokens
    profile_info = None
    working_token = None
    
    for idx, token in enumerate(visit_tokens_to_try):
        print(f"Attempting profile info with visit token {idx + 1}")
        profile_info = make_profile_check_request(encrypted_player_uid_for_profile, server_name_param, token)
        if profile_info and hasattr(profile_info, 'AccountInfo'):
            working_token = token
            print(f"✓ Success with visit token {idx + 1}")
            break
        else:
            print(f"✗ Visit token {idx + 1} failed, trying next...")
            time.sleep(0.2)
    
    if not profile_info or not hasattr(profile_info, 'AccountInfo'):
        return jsonify({"error": "Could not fetch profile information"}), 404
    
    # Extract profile data
    account_info = profile_info.AccountInfo
    response_data = {
        "UID": int(account_info.UID),
        "PlayerNickname": str(account_info.PlayerNickname) if account_info.PlayerNickname else "N/A",
        "Likes": int(account_info.Likes),
        "Region": str(account_info.Region) if hasattr(account_info, 'Region') else "N/A",
        "Level": int(account_info.Level) if hasattr(account_info, 'Level') else 0,
        "Rank": int(account_info.Rank) if hasattr(account_info, 'Rank') else 0,
        "Server": server_name_param,
        "TokenUsed": working_token.get("token", "")[:20] + "..." if working_token else "None",
        "Note": f"Profile fetched using {'random' if use_random_visit else 'sequential'} visit tokens (tried {len(visit_tokens_to_try)} tokens)"
    }
    
    return jsonify(response_data)

@app.route('/token_info', methods=['GET'])
def token_info():
    """Endpoint to check token counts for each server"""
    servers = ["IND", "BD", "BR", "US", "SAC", "NA"]
    info = {}
    
    for server in servers:
        regular_tokens = load_tokens(server, for_visit=False)
        visit_tokens = load_tokens(server, for_visit=True)
        info[server] = {
            "regular_tokens": len(regular_tokens),
            "visit_tokens": len(visit_tokens)
        }
    
    return jsonify(info)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=False)