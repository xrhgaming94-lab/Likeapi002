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

# ================= CONFIG =================
TOKEN_BATCH_SIZE = 189
RELEASE_VERSION = "OB52"   # 🔥 OB52 GLOBAL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= GLOBAL STATE =================
current_batch_indices = {}
batch_indices_lock = threading.Lock()

# ================= TOKEN BATCHING =================
def get_next_batch_tokens(server_name, all_tokens):
    if not all_tokens:
        return []

    total_tokens = len(all_tokens)
    if total_tokens <= TOKEN_BATCH_SIZE:
        return all_tokens

    with batch_indices_lock:
        if server_name not in current_batch_indices:
            current_batch_indices[server_name] = 0

        start = current_batch_indices[server_name]
        end = start + TOKEN_BATCH_SIZE

        if end > total_tokens:
            batch = all_tokens[start:] + all_tokens[:end - total_tokens]
        else:
            batch = all_tokens[start:end]

        current_batch_indices[server_name] = (start + TOKEN_BATCH_SIZE) % total_tokens
        return batch

def get_random_batch_tokens(server_name, all_tokens):
    if not all_tokens:
        return []
    if len(all_tokens) <= TOKEN_BATCH_SIZE:
        return all_tokens.copy()
    return random.sample(all_tokens, TOKEN_BATCH_SIZE)

# ================= TOKEN LOADER =================
def load_tokens(server_name, for_visit=False):
    if for_visit:
        path = (
            "token_ind_visit.json" if server_name == "IND"
            else "token_br_visit.json" if server_name in {"BR", "US", "SAC", "NA"}
            else "token_bd_visit.json"
        )
    else:
        path = (
            "token_ind.json" if server_name == "IND"
            else "token_br.json" if server_name in {"BR", "US", "SAC", "NA"}
            else "token_bd.json"
        )

    try:
        with open(path, "r") as f:
            tokens = json.load(f)
            return tokens if isinstance(tokens, list) else []
    except Exception:
        return []

# ================= ENCRYPTION =================
def encrypt_message(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return binascii.hexlify(cipher.encrypt(pad(plaintext, AES.block_size))).decode()

# ================= PROTOBUF =================
def create_like_proto(uid, region):
    msg = like_pb2.like()
    msg.uid = int(uid)
    msg.region = region
    return msg.SerializeToString()

def create_profile_proto(uid):
    msg = uid_generator_pb2.uid_generator()
    msg.krishna_ = int(uid)
    msg.teamXdarks = 1
    return msg.SerializeToString()

def enc_profile_payload(uid):
    return encrypt_message(create_profile_proto(uid))

# ================= LIKE REQUEST =================
async def send_single_like(encrypted_payload, token_dict, url):
    token = token_dict.get("token")
    if not token:
        return 999

    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; Android 9)",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=bytes.fromhex(encrypted_payload),
                headers=headers,
                timeout=10
            ) as r:
                return r.status
    except:
        return 998

async def send_like_batch(uid, region, url, tokens):
    payload = encrypt_message(create_like_proto(uid, region))
    tasks = [send_single_like(payload, t, url) for t in tokens]
    return await asyncio.gather(*tasks)

# ================= PROFILE CHECK =================
def profile_check(enc_payload, server, token_dict):
    token = token_dict.get("token")
    if not token:
        return None

    url = (
        "https://client.ind.freefiremobile.com/GetPlayerPersonalShow" if server == "IND"
        else "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
        if server in {"BR", "US", "SAC", "NA"}
        else "https://clientbp.ggblueshark.com/GetPlayerPersonalShow"
    )

    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; Android 9)",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION  # ✅ OB52
    }

    try:
        r = requests.post(url, data=bytes.fromhex(enc_payload), headers=headers, timeout=10, verify=False)
        info = like_count_pb2.Info()
        info.ParseFromString(r.content)
        return info
    except:
        return None

# ================= FLASK APP =================
app = Flask(__name__)

@app.route("/like", methods=["GET"])
def like_api():
    uid = request.args.get("uid")
    server = request.args.get("server_name", "").upper()
    use_random = request.args.get("random", "false") == "true"

    if not uid or not server:
        return jsonify({"error": "uid & server_name required"}), 400

    visit_tokens = load_tokens(server, True)
    like_tokens = load_tokens(server, False)

    if not visit_tokens or not like_tokens:
        return jsonify({"error": "Tokens missing"}), 500

    batch = (
        get_random_batch_tokens(server, like_tokens)
        if use_random else get_next_batch_tokens(server, like_tokens)
    )

    enc_uid = enc_profile_payload(uid)
    before = profile_check(enc_uid, server, visit_tokens[0])
    before_likes = before.AccountInfo.Likes if before else 0

    like_url = (
        "https://client.ind.freefiremobile.com/LikeProfile" if server == "IND"
        else "https://client.us.freefiremobile.com/LikeProfile"
        if server in {"BR", "US", "SAC", "NA"}
        else "https://clientbp.ggblueshark.com/LikeProfile"
    )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(send_like_batch(uid, server, like_url, batch))
    loop.close()

    after = profile_check(enc_uid, server, visit_tokens[0])
    after_likes = after.AccountInfo.Likes if after else before_likes

    return jsonify({
        "UID": uid,
        "LikesBefore": before_likes,
        "LikesAfter": after_likes,
        "LikesAdded": after_likes - before_likes,
        "OB": RELEASE_VERSION
    })

@app.route("/token_info")
def token_info():
    servers = ["IND", "BD", "BR", "US", "SAC", "NA"]
    return jsonify({
        s: {
            "regular": len(load_tokens(s)),
            "visit": len(load_tokens(s, True))
        } for s in servers
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)
