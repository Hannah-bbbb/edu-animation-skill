#!/usr/bin/env python3
"""Plan, submit once, resume and download one-character Ark animation jobs."""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

CONFIG = {"base_url": "https://ark.cn-beijing.volces.com/api/v3"}
TERMINAL = {"succeeded", "failed", "cancelled", "expired"}

def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".state-", delete=False) as f:
        temporary = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def project_file(root, value):
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("路径必须位于当前项目内")
    return path


@contextlib.contextmanager
def job_lock(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock = state_path.with_suffix(".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("此任务已有操作锁；请先确认没有其他生成/下载进程，不要并发重复提交")
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def read_key():
    key = os.environ.get("ARK_API_KEY", "").strip()
    if not key or key.lower() in {"key", "your_api_key"} or any(c.isspace() for c in key):
        raise ValueError("Set ARK_API_KEY in the environment; do not store it in recipes")
    return key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the Ark authorization header to another host.


def validate_generation_format(request):
    if not isinstance(request, dict) or request.get("resolution") != "480p":
        raise ValueError("付费生成铁律：必须显式使用 resolution=480p")
    if type(request.get("duration")) is not int or request["duration"] != 4:
        raise ValueError("付费生成铁律：duration 只能为4秒整数")
    if "frames" in request:
        raise ValueError("不能用 frames 覆盖固定4秒时长")


def api_json(method, base_url, key, task_id=None, payload=None):
    if method == "POST":
        validate_generation_format(payload)
    if base_url != "https://ark.cn-beijing.volces.com/api/v3":
        raise ValueError("当前工具仅连接配置的火山方舟北京官方 API 地址")
    url = base_url + "/contents/generations/tasks"
    if task_id:
        url += "/" + urllib.parse.quote(task_id, safe="")
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    # POST is deliberately sent once. A timeout does not prove no task was created.
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            value = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            code = json.loads(error.read(65536)).get("error", {}).get("code", "")
        except (ValueError, AttributeError):
            code = ""
        code = str(code) if re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", str(code)) else ""
        raise ValueError(f"方舟 API HTTP {error.code} {code}；请检查 key、权限、参数及控制台任务，不会自动重发") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ValueError("方舟 API 请求未得到可确认的结果；检查控制台任务后再继续，不要直接新建重复任务") from None
    if not isinstance(value, dict):
        raise ValueError("方舟 API 返回格式不正确")
    return value


def load_job(root, filename):
    recipe = json.loads(project_file(root, filename).read_text(encoding="utf-8"))
    allowed = {"job_id", "character", "action", "label", "purpose", "playback",
               "reference_image", "reference_sha256", "prompt", "request", "ratio_reason"}
    if not isinstance(recipe, dict) or set(recipe) - allowed:
        raise ValueError("Unknown recipe fields")
    patterns = {"job_id": r"[a-z0-9]+(?:-[a-z0-9]+)*", "character": r"[a-z][a-z0-9_-]*",
                "action": r"[a-z][a-z0-9_]*", "label": r"[\w\u4e00-\u9fff-]+"}
    for name, pattern in patterns.items():
        value = recipe.get(name)
        if not isinstance(value, str) or not 1 <= len(value) <= 64 or not re.fullmatch(pattern, value):
            raise ValueError("Invalid naming field: " + name)
    if not isinstance(recipe.get("purpose"), str) or not recipe["purpose"].strip():
        raise ValueError("purpose is required")
    if recipe.get("playback") not in {"loop", "return_idle", "hold"}:
        raise ValueError("playback must be loop / return_idle / hold")
    prompt = recipe.get("prompt", "")
    if not isinstance(prompt, str) or any(x in prompt for x in ("{{", "}}", "REPLACE_")):
        raise ValueError("Fill the complete prompt before planning")
    required = ("【角色一致性】", "【动作要求】", "【画面与背景】", "【禁止事项】", "#00FF00")
    if any(x not in prompt for x in required):
        raise ValueError("Prompt requires all four sections and #00FF00")
    request = recipe.get("request")
    allowed_request = {"model", "resolution", "duration", "ratio", "generate_audio", "watermark"}
    if not isinstance(request, dict) or set(request) != allowed_request:
        raise ValueError("request must contain exactly model/resolution/duration/ratio/generate_audio/watermark")
    request = dict(request)
    validate_generation_format(request)
    if not isinstance(request["model"], str) or not request["model"].strip():
        raise ValueError("model is required")
    if request["ratio"] not in {"1:1", "16:9", "9:16", "4:3", "3:4", "21:9"}:
        raise ValueError("Use a fixed supported ratio")
    if request["ratio"] != "1:1" and not str(recipe.get("ratio_reason", "")).strip():
        raise ValueError("Non-square ratio requires ratio_reason")
    if request["generate_audio"] is not False or request["watermark"] is not False:
        raise ValueError("This character-clip tool requires generate_audio=false and watermark=false")
    reference_name = recipe.get("reference_image")
    if not isinstance(reference_name, str):
        raise ValueError("reference_image must be a workspace-relative file")
    reference = project_file(root, reference_name)
    if not reference.is_file() or reference.stat().st_size >= 30 * 1024 * 1024:
        raise ValueError("Reference must exist and be below 30 MiB")
    data = reference.read_bytes()
    if digest(data) != recipe.get("reference_sha256"):
        raise ValueError("Reference SHA256 mismatch; select and pin the intended design")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise ValueError("Reference must be a PNG or JPEG")
    request["content"] = [{"type": "text", "text": prompt}, {
        "type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + base64.b64encode(data).decode()},
        "role": "reference_image"}]
    references = [{"path": str(reference.relative_to(root)), "sha256": digest(data)}]
    stem = "__".join(recipe[k] for k in ("character", "action", "label", "job_id"))
    if len((stem + ".source.json").encode()) > 250:
        raise ValueError("Output name is too long")
    source = project_file(root, "sources/animations/" + recipe["character"] + "/" + stem + ".mp4")
    state = project_file(root, "runtime/ark/" + recipe["job_id"] + "/task.json")
    return recipe, request, references, source, state


def load_state(path, recipe, request):
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    identity = {key: recipe[key] for key in ("job_id", "character", "action", "label", "purpose", "playback")}
    if state.get("identity") != identity or state.get("request_sha256") != fingerprint(request):
        raise ValueError("同一 job_id 的生成请求或用途已改变；请使用新的 job_id，原任务不会被覆盖")
    return state


def submit(path, recipe, request, references, key, config):
    state = load_state(path, recipe, request)
    if state:
        if not state.get("task_id"):
            raise ValueError("此前提交结果不明。请先在方舟控制台查找任务，再用 attach 绑定其ID；不会重复提交")
        return state
    state = {"identity": {k: recipe[k] for k in ("job_id", "character", "action", "label", "purpose", "playback")},
             "request_sha256": fingerprint(request), "references": references, "recipe": recipe,
             "status": "submitting"}
    write_json(path, state)
    try:
        result = api_json("POST", config["base_url"], key, payload=request)
        task_id = result.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("未返回任务 ID，请在方舟控制台查询，不要重复提交")
        state.update(task_id=task_id, status="queued")
    except (ValueError, OSError):
        state["status"] = "submission_unknown"
        write_json(path, state)
        raise
    write_json(path, state)
    return state


def query(path, state, key, config):
    result = api_json("GET", config["base_url"], key, task_id=state["task_id"])
    if result.get("id") != state["task_id"]:
        raise ValueError("查询响应的任务 ID 不匹配")
    status = result.get("status")
    if status not in TERMINAL | {"queued", "running"}:
        raise ValueError("查询返回未知任务状态")
    state.update(status=status, response=result)
    write_json(path, state)
    return state


def download_video(url, destination):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("下载链接必须为 HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Signed result URLs need no Ark key. Never send Authorization to a CDN.
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".download-", suffix=".mp4", delete=False) as f:
        temp = Path(f.name)
    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=45) as response, temp.open("wb") as f:
            total = 0
            while data := response.read(1024 * 1024):
                total += len(data)
                if total > 1024 ** 3:
                    raise ValueError("原片超过1GiB，停止下载")
                f.write(data)
        with temp.open("rb") as f:
            header = f.read(32)
        if total < 32 or b"ftyp" not in header:
            raise ValueError("下载结果不是有效的 MP4 文件头")
        if destination.exists():
            raise ValueError("目标原片已存在，拒绝覆盖")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def fetch_original(root, path, state, source):
    sidecar = source.with_suffix(".source.json")
    # Recover the narrow case where both files were published but task state was
    # not yet updated. Unknown files never count as our previous download.
    if state.get("source") or source.is_file() and sidecar.is_file():
        provenance = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        expected = state.get("source_sha256", provenance.get("source_sha256"))
        if not source.is_file() or digest(source.read_bytes()) != expected:
            raise ValueError("已保存原片缺失或字节变化；不自动覆盖")
        if provenance.get("task_id") != state["task_id"] or provenance.get("request_sha256") != state["request_sha256"]:
            raise ValueError("原片来源记录缺失或不匹配；请核查后恢复记录，不覆盖原片")
        state.update(source=str(source.relative_to(root)), source_sha256=expected)
        write_json(path, state)
        return state
    if state["status"] != "succeeded":
        if state["status"] in TERMINAL:
            error = state.get("response", {}).get("error") or {}
            raise ValueError(f"生成任务 {state['status']}：{error.get('code', '请查看 task.json')}；不会自动新建任务")
        return state
    url = state.get("response", {}).get("content", {}).get("video_url")
    if not isinstance(url, str):
        raise ValueError("成功响应缺少 content.video_url")
    if source.exists() or sidecar.exists():
        raise ValueError("原片/来源文件已存在，拒绝覆盖；请核对是否为上次中断留下的文件")
    download_video(url, source)
    state.update(source=str(source.relative_to(root)), source_sha256=digest(source.read_bytes()))
    # Store the recipe and hashes, not expanded Base64 blobs or credentials.
    write_json(sidecar, {"generator": "edu-animation-skill/ark_video", "task_id": state["task_id"],
                        "request_sha256": state["request_sha256"], "source_sha256": state["source_sha256"],
                        "recipe": state["recipe"], "references": state["references"],
                        "review": "pending"})
    write_json(path, state)
    return state


def execute(args):
    root = args.workspace.expanduser().resolve(strict=True)
    recipe, request, references, source, state_path = load_job(root, args.job)
    plan_sha = fingerprint({"recipe": recipe, "request_sha256": fingerprint(request)})
    if args.command == "plan":
        return {"job_id": recipe["job_id"], "purpose": recipe["purpose"], "playback": recipe["playback"],
                "plan_sha256": plan_sha, "request": recipe["request"], "prompt": recipe["prompt"],
                "references": references, "source": str(source.relative_to(root)), "network_called": False}
    if args.command == "generate" and args.plan_sha256 != plan_sha:
        raise ValueError("Run plan, inspect prompt/reference/format, then pass its exact --plan-sha256")
    with job_lock(state_path):
        state = load_state(state_path, recipe, request)
        if args.command == "attach":
            if state and state.get("task_id"):
                raise ValueError("Task already attached")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", args.task_id or ""):
                raise ValueError("Use the exact console task ID")
            result = api_json("GET", CONFIG["base_url"], read_key(), task_id=args.task_id)
            if result.get("id") != args.task_id or result.get("status") not in TERMINAL | {"queued", "running"}:
                raise ValueError("Task identity/status mismatch")
            state = state or {"identity": {k: recipe[k] for k in ("job_id", "character", "action", "label", "purpose", "playback")},
                              "request_sha256": fingerprint(request), "references": references, "recipe": recipe}
            state.update(task_id=args.task_id, status=result["status"], response=result)
            write_json(state_path, state)
        elif args.command == "generate":
            state = submit(state_path, recipe, request, references, read_key(), CONFIG)
        if not state or not state.get("task_id"):
            raise ValueError("No confirmed task ID; use generate or recover the same task with attach")
        if args.command in {"status", "download"} and not state.get("source"):
            state = query(state_path, state, read_key(), CONFIG)
        if args.command == "download":
            state = fetch_original(root, state_path, state, source)
        if state["status"] in {"failed", "cancelled", "expired"}:
            raise ValueError("Task ended: " + state["status"] + "; inspect saved response; no automatic resubmission")
        return {"job_id": recipe["job_id"], "task_id": state["task_id"], "status": state["status"],
                "state": str(state_path.relative_to(root)), "source": state.get("source"), "review": "pending"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "generate", "status", "download", "attach"])
    parser.add_argument("job", type=Path, help="Recipe path relative to workspace")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--plan-sha256", help="Exact hash returned by plan, required for generate")
    parser.add_argument("--task-id", help="Only attach: manually matched existing console task")
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
