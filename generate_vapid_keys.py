import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode("ascii")

numbers = public_key.public_numbers()
x = numbers.x.to_bytes(32, "big")
y = numbers.y.to_bytes(32, "big")
public_b64 = b64url(b"\x04" + x + y)

print("VAPID_PUBLIC_KEY=" + public_b64)
print("VAPID_PRIVATE_KEY=" + private_pem.replace("\n", "\\n"))
