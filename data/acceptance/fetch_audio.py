"""Stream only the first few entries of a FLEURS test tarball (no full download)."""
import os, sys, tarfile, urllib.request

lang, outdir, want = sys.argv[1], sys.argv[2], int(sys.argv[3])
url = f"https://huggingface.co/datasets/google/fleurs/resolve/main/data/{lang}/audio/test.tar.gz"
os.makedirs(outdir, exist_ok=True)
resp = urllib.request.urlopen(url, timeout=60)
tf = tarfile.open(fileobj=resp, mode="r|gz")
saved = []
for member in tf:
    if not member.isfile() or not member.name.endswith(".wav"):
        continue
    name = os.path.basename(member.name)
    data = tf.extractfile(member).read()
    with open(os.path.join(outdir, name), "wb") as handle:
        handle.write(data)
    saved.append((name, len(data)))
    if len(saved) >= want:
        break
try:
    resp.close()
except Exception:
    pass
print("downloaded", len(saved), "files")
for name, size in saved:
    print(name, size)
