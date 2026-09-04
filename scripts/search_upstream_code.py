import urllib.request
import json

def search_repo(repo, query):
    print(f"\n=== Searching {repo} for '{query}' ===")
    url = f"https://api.github.com/search/code?q=repo:{repo}+{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "Antigravity/1.0", "Accept": "application/vnd.github.v3+json"})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            items = data.get("items", [])
            print(f"Total count: {data.get('total_count', 0)}")
            for item in items[:10]:
                print(f"  {item['path']}")
    except Exception as e:
        print(f"Search failed: {e}")

search_repo("SideStore/SideStore", "IfManager")
search_repo("SideStore/SideStore", "nextProbableSideVPN")
search_repo("SideStore/minimuxer", "IfManager")
search_repo("SideStore/minimuxer", "nextProbableSideVPN")
search_repo("SideStore/minimuxer", "10.7.0.1")
search_repo("SideStore/SideStore", "10.7.0.1")
