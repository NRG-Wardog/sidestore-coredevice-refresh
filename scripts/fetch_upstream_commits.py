import urllib.request
import json

def fetch_commits(repo):
    print(f"\n=== Commits for {repo} ===")
    url = f"https://api.github.com/repos/{repo}/commits?per_page=15"
    req = urllib.request.Request(url, headers={"User-Agent": "Antigravity/1.0"})
    try:
        with urllib.request.urlopen(req) as resp:
            commits = json.loads(resp.read())
            for c in commits:
                msg = c["commit"]["message"].split("\n")[0]
                print(f"{c['sha'][:8]} {c['commit']['committer']['date'][:10]} {msg}")
    except Exception as e:
        print(f"Failed: {e}")

fetch_commits("SideStore/SideStore")
fetch_commits("SideStore/minimuxer")
fetch_commits("SideStore/idevice")
