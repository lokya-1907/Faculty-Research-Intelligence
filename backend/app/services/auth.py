import base64
import hashlib
import hmac
import json
import secrets
import time

from app.core.config import settings


def _encode(payload):
    body=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).rstrip(b'=')
    signature=hmac.new(settings.auth_secret.encode(),body,hashlib.sha256).digest()
    return body.decode()+'.'+base64.urlsafe_b64encode(signature).rstrip(b'=').decode()


def _decode(token):
    try:
        body,encoded_signature=token.split('.',1)
        padded=body+'='*((4-len(body)%4)%4)
        signature=base64.urlsafe_b64decode(encoded_signature+'='*((4-len(encoded_signature)%4)%4))
        expected=hmac.new(settings.auth_secret.encode(),body.encode(),hashlib.sha256).digest()
        if not hmac.compare_digest(signature,expected):
            return None
        payload=json.loads(base64.urlsafe_b64decode(padded))
        if int(payload.get('exp',0)) < int(time.time()):
            return None
        return payload
    except (ValueError,TypeError,KeyError,json.JSONDecodeError):
        return None


def authenticate(username,password):
    if not hmac.compare_digest(username,settings.auth_username):
        return None
    if not hmac.compare_digest(password,settings.auth_password):
        return None
    return _encode({'sub':username,'iat':int(time.time()),'exp':int(time.time())+settings.auth_session_hours*3600,'nonce':secrets.token_hex(8)})


def verify_token(token):
    return _decode(token)
