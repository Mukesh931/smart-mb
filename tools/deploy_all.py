"""
Smart-MB :: scripted GitHub + Render deployment.

Reads credentials from the environment only — nothing is written to disk and no
token is ever committed:

    export GITHUB_TOKEN=ghp_...        # classic PAT with 'repo' scope, or fine-grained
                                       # with Contents: Read+Write + Administration: Read+Write
    export RENDER_API_KEY=rnd_...      # Render → Account Settings → API Keys

Usage:
    python3 -m tools.deploy_all github            # create the repo + push main
    python3 -m tools.deploy_all render            # create/refresh the Render web service
    python3 -m tools.deploy_all all               # both, then smoke-test the live URL
    python3 -m tools.deploy_all render --dry-run  # print the exact API payload, no network

Options:
    --repo-name smart-mb        GitHub repository name
    --visibility public|private
    --service-name smart-mb     Render service name
    --plan free|starter|...     Render compute plan (free has no persistent disk)
    --region singapore|oregon|frankfurt|ohio|virginia
    --disk / --no-disk          attach a 1 GB disk at /var/data (paid plans only)
    --branch main
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH_API = "https://api.github.com"
RENDER_API = "https://api.render.com/v1"

BUILD_CMD = "pip install --upgrade pip && pip install -r requirements.txt"
START_CMD = "python3 -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level warning"


# ----------------------------------------------------------------- tiny HTTP
class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:400]}")


def request(url: str, *, method: str = "GET", token: str = "", body=None, accept: str = "application/json"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", accept)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw.strip().startswith((b"{", b"[")) else raw.decode())
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode(errors="replace")) from None


def sh(cmd: list[str], **kw) -> str:
    print(f"  $ {' '.join(c if 'x-access-token' not in c else 'git push <redacted>' for c in cmd)}")
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)
    if out.returncode:
        raise RuntimeError(f"command failed ({out.returncode}): {out.stderr.strip()[:600]}")
    return out.stdout.strip()


# ------------------------------------------------------------------- GitHub
def deploy_github(args) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.exit("GITHUB_TOKEN is not set. Create a PAT (repo scope) and export it.")

    print("[github] verifying token")
    _, user = request(f"{GH_API}/user", token=token)
    owner = user["login"]
    print(f"[github] authenticated as {owner}")

    full = f"{owner}/{args.repo_name}"
    print(f"[github] ensuring repository {full} ({args.visibility})")
    try:
        _, repo = request(f"{GH_API}/user/repos", method="POST", token=token, body={
            "name": args.repo_name,
            "description": "Smart-MB — Centralized CSR Database & Site Verification System "
                           "for Maharashtra PWD Electrical (FastAPI + SQLite, Form-23 MB engine)",
            "private": args.visibility == "private",
            "has_issues": True, "has_wiki": False, "has_projects": False,
            "auto_init": False,
        })
        print(f"[github] created {repo['html_url']}")
    except ApiError as exc:
        if exc.status == 422:                      # already exists
            print("[github] repository already exists — reusing it")
            _, repo = request(f"{GH_API}/repos/{full}", token=token)
        else:
            raise

    print("[github] pushing main")
    clone = repo.get("clone_url") or f"https://github.com/{full}.git"
    push_url = clone.replace("https://", f"https://x-access-token:{token}@")
    sh(["git", "remote", "remove", "origin"], check=False) if _has_remote("origin") else None
    sh(["git", "remote", "add", "origin", clone])
    sh(["git", "push", push_url, f"HEAD:refs/heads/{args.branch}", "--force"])
    # keep the stored remote credential-free and track the pushed branch
    sh(["git", "fetch", "origin", args.branch], check=False)
    sh(["git", "branch", f"--set-upstream-to=origin/{args.branch}", args.branch], check=False)

    print("[github] setting topics + homepage")
    request(f"{GH_API}/repos/{full}/topics", method="PUT", token=token,
            accept="application/vnd.github+json",
            body={"names": ["fastapi", "pwd", "maharashtra", "csr", "schedule-of-rates", "measurement-book",
                            "form-23", "construction", "govtech", "sqlite", "reportlab"]})
    return {"owner": owner, "repo": args.repo_name, "url": repo["html_url"], "clone": clone}


def _has_remote(name: str) -> bool:
    out = subprocess.run(["git", "remote"], cwd=ROOT, capture_output=True, text=True)
    return name in out.stdout.split()


# ------------------------------------------------------------------- Render
def render_owner_id(token: str) -> str:
    _, owners = request(f"{RENDER_API}/owners?limit=20", token=token)
    if not owners:
        sys.exit("This Render account has no workspace (owner) available.")
    owner = owners[0].get("owner", owners[0])
    print(f"[render] workspace: {owner.get('name')} ({owner['id']})")
    return owner["id"]


def render_service_payload(args, owner_id: str, repo_url: str) -> dict:
    details = {
        "runtime": "python",
        "plan": args.plan,
        "region": args.region,
        "healthCheckPath": "/api/health",
        "envSpecificDetails": {"buildCommand": BUILD_CMD, "startCommand": START_CMD},
        "numInstances": 1,
    }
    if args.disk:
        details["disk"] = {"name": "smart-mb-data", "mountPath": "/var/data", "sizeGB": 1}
    env_vars = [
        {"key": "PYTHON_VERSION", "value": "3.13.0"},
        {"key": "PYTHONUNBUFFERED", "value": "1"},
        {"key": "SMARTMB_SECRET", "generateValue": True},
    ]
    if args.disk:
        env_vars.append({"key": "SMARTMB_DATA_DIR", "value": "/var/data"})
    return {
        "type": "web_service",
        "name": args.service_name,
        "ownerId": owner_id,
        "repo": repo_url,
        "branch": args.branch,
        "autoDeploy": "yes",
        "envVars": env_vars,
        "serviceDetails": details,
    }


def deploy_render(args, repo_url: str = "") -> dict:
    token = os.environ.get("RENDER_API_KEY", "").strip()
    repo_url = repo_url or args.repo_url or f"https://github.com/{args.gh_owner or '<owner>'}/{args.repo_name}"

    if args.dry_run:
        owner_id = "<resolved from GET /v1/owners at deploy time>"
        payload = render_service_payload(args, owner_id, repo_url)
        print("\n[render] DRY RUN — would POST /v1/services with:")
        print(json.dumps(payload, indent=2))
        return {"dry_run": True, "would_post": payload}

    if not token:
        sys.exit("RENDER_API_KEY is not set. Render → Account Settings → API Keys → create key.")
    owner_id = render_owner_id(token)
    payload = render_service_payload(args, owner_id, repo_url)

    print(f"[render] looking up an existing service named '{args.service_name}'")
    try:
        _, listing = request(f"{RENDER_API}/services?name={args.service_name}&limit=5", token=token)
    except ApiError:
        listing = []
    existing = None
    for item in listing or []:
        svc = item.get("service", item)
        if svc.get("name") == args.service_name and svc.get("type") == "web_service":
            existing = svc
            break
    deploy_id = ""

    if existing:
        svc_id = existing["id"]
        print(f"[render] service exists ({svc_id}) — refreshing settings and deploying")
        request(f"{RENDER_API}/services/{svc_id}", method="PATCH", token=token, body={
            "autoDeploy": "yes", "branch": args.branch,
            "serviceDetails": payload["serviceDetails"],
        })
        _, deploy = request(f"{RENDER_API}/services/{svc_id}/deploys", method="POST", token=token,
                            body={"clearCache": "do_not_clear"})
        dep = (deploy or {}).get("deploy", deploy) or {}
        deploy_id = dep.get("id")
    else:
        print("[render] creating web service")
        status, created = request(f"{RENDER_API}/services", method="POST", token=token, body=payload)
        svc_id = created["service"]["id"]
        deploy_id = created.get("deployId")            # create-service returns {service, deployId}
        print(f"[render] created {svc_id} — dashboard: {created['service'].get('dashboardUrl', '')}")
        if not deploy_id:
            _, deploy = request(f"{RENDER_API}/services/{svc_id}/deploys", method="POST", token=token,
                                body={"clearCache": "do_not_clear"})
            dep = (deploy or {}).get("deploy", deploy) or {}
            deploy_id = dep.get("id")
    print(f"[render] deploy {deploy_id} started — waiting for it to go live")
    url = ""
    deadline = time.time() + args.wait
    while time.time() < deadline:
        time.sleep(12)
        _, svc = request(f"{RENDER_API}/services/{svc_id}", token=token)
        svc = svc.get("service", svc) if isinstance(svc, dict) else svc
        url = (svc.get("serviceDetails") or {}).get("url") or url
        if deploy_id:
            _, d = request(f"{RENDER_API}/services/{svc_id}/deploys/{deploy_id}", token=token)
            d = d.get("deploy", d) if isinstance(d, dict) else d
            status = d.get("status")
            print(f"         … {status}")
            if status == "live":
                break
            if status in ("build_failed", "update_failed", "canceled", "pre_deploy_failed"):
                print("[render] deploy did not succeed — last logs:")
                try:
                    _, logs = request(f"{RENDER_API}/services/{svc_id}/logs?limit=60", token=token)
                    for line in (logs.get("logs") or [])[-25:]:
                        print("         " + str(line.get("message", line))[:220])
                except ApiError as exc:
                    print(f"         (log fetch failed: {exc})")
                sys.exit(f"Render deploy ended with status '{status}'.")
    return {"service_id": svc_id, "deploy_id": deploy_id, "url": url}


def smoke(url: str) -> bool:
    print(f"[verify] GET {url}/api/health")
    for attempt in range(12):
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=45) as res:
                body = json.loads(res.read())
            print(f"[verify] OK — {body}")
            with urllib.request.urlopen(url.rstrip("/") + "/", timeout=45) as res:
                html = res.read().decode(errors="replace")
            ok = res.status == 200 and "Smart-MB" in html and "app.js" in html
            print(f"[verify] SPA served: {res.status} · {len(html) // 1024} KiB · "
                  f"app shell intact: {ok}")
            for asset in ("/style.css", "/app.js"):
                with urllib.request.urlopen(url.rstrip("/") + asset, timeout=45) as a:
                    print(f"[verify] {asset} -> {a.status} ({len(a.read()) // 1024} KiB)")
            return True
        except Exception as exc:                                    # noqa: BLE001
            print(f"         attempt {attempt + 1}/12: {exc}")
            time.sleep(10)
    return False


# ------------------------------------------------------------------- status
def status(args) -> dict:
    token = os.environ.get("RENDER_API_KEY", "").strip()
    if not token:
        sys.exit("RENDER_API_KEY is not set.")
    _, listing = request(f"{RENDER_API}/services?name={args.service_name}&limit=5", token=token)
    svc = None
    for item in listing or []:
        cand = item.get("service", item)
        if cand.get("name") == args.service_name:
            svc = cand
            break
    if not svc:
        sys.exit(f"No Render service named '{args.service_name}' in this workspace.")
    _, full = request(f"{RENDER_API}/services/{svc['id']}", token=token)
    full = full.get("service", full)
    details = full.get("serviceDetails") or {}
    url = details.get("url", "")
    _, deploys = request(f"{RENDER_API}/services/{svc['id']}/deploys?limit=3", token=token)
    print(f"service : {full['name']} ({full['id']})")
    print(f"url     : {url}")
    print(f"plan    : {details.get('plan')} · region {details.get('region')} · "
          f"autoDeploy {full.get('autoDeployTrigger') or full.get('autoDeploy')}")
    print(f"disk    : {details.get('disk') or 'none — the SQLite database resets on every spin-down/redeploy'}")
    for item in deploys or []:
        dep = item.get("deploy", item)
        print(f"deploy  : {dep.get('id')} {dep.get('status'):<12} {(dep.get('commit') or {}).get('message', '')[:60]}")
    if url:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=60) as res:
                print(f"health  : {json.loads(res.read())}")
        except Exception as exc:                                     # noqa: BLE001
            print(f"health  : unreachable ({exc}) — free instances sleep after 15 min idle and cold-start in ~1 min")
    return {"service_id": svc["id"], "url": url}


# --------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy Smart-MB to GitHub and Render")
    ap.add_argument("target", choices=["github", "render", "all", "status"])
    ap.add_argument("--repo-name", default="smart-mb")
    ap.add_argument("--gh-owner", default="", help="GitHub owner (used to build the Render repo URL)")
    ap.add_argument("--repo-url", default="", help="explicit repo URL for Render")
    ap.add_argument("--visibility", default="public", choices=["public", "private"])
    ap.add_argument("--service-name", default="smart-mb")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--plan", default="free")
    ap.add_argument("--region", default="singapore",
                    choices=["singapore", "oregon", "ohio", "virginia", "frankfurt"])
    ap.add_argument("--disk", dest="disk", action="store_true", default=False,
                    help="attach a 1 GB persistent disk at /var/data (paid plans only)")
    ap.add_argument("--no-disk", dest="disk", action="store_false")
    ap.add_argument("--wait", type=int, default=900, help="seconds to wait for the deploy")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    result: dict = {}
    if args.target == "status":
        print(json.dumps(status(args), indent=2))
        return
    if args.target in ("github", "all"):
        result["github"] = deploy_github(args)
        if not args.gh_owner:
            args.gh_owner = result["github"]["owner"]
    if args.target in ("render", "all"):
        repo_url = result.get("github", {}).get("clone", "") or args.repo_url
        if args.disk and args.plan == "free":
            sys.exit("Render's free plan cannot attach a persistent disk — use --plan starter "
                     "(requires payment details) or run without --disk.")
        result["render"] = deploy_render(args, repo_url)
        url = result["render"].get("url", "")
        if url and not args.dry_run:
            result["verified"] = smoke(url)

    print("\n" + "=" * 68)
    print(json.dumps(result, indent=2))
    if result.get("github", {}).get("url"):
        print(f"\nGitHub : {result['github']['url']}")
    if result.get("render", {}).get("url"):
        print(f"Live   : {result['render']['url']}")
        print("\nSign in as admin@pwd.maharashtra.gov.in / Admin@123 and CHANGE THE PASSWORD, "
              "then import the official CSR files (see docs/DEPLOY.md).")


if __name__ == "__main__":
    main()
