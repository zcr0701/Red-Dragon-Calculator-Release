import requests
import os
import json

# ========== 配置区 ==========
BASE_URL = "https://formulabmit-api-pcztinxqaq.cn-hangzhou.fcapp.run"
TOKEN = "RedDragon#5854"
DOWNLOAD_DIR = "./downloaded_records"    # 本地保存 JSON 的目录
PROGRESS_FILE = os.path.join(DOWNLOAD_DIR, "progress.json")  # 记录已下载 task_id 列表
# ============================

def load_progress():
    """读取已下载的 task_id 集合"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return set(data.get("downloaded", []))
    return set()

def save_progress(downloaded_set):
    """保存已下载的 task_id 集合到本地文件"""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump({"downloaded": list(downloaded_set)}, f, ensure_ascii=False, indent=2)

def get_all_task_ids():
    """从云端获取全部 task_id 列表（自动处理分页，假设每次最多返回1000条）"""
    all_task_ids = []
    headers = {"Authorization": f"Bearer {TOKEN}"}
    # 先尝试一次性获取足够多的记录（可根据实际情况调整 limit）
    # 如果你的数据量非常大，可以改成循环分页，这里先简单处理
    try:
        resp = requests.get(
            f"{BASE_URL}/api/list?limit=10000",  # 假设总记录不超过10000
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            all_task_ids = data.get("task_ids", [])
            print(f"云端共有 {data.get('total', 0)} 条记录，本次拉取到 {len(all_task_ids)} 个 ID")
        else:
            print(f"获取列表失败: {resp.status_code} - {resp.text}")
            return []
    except Exception as e:
        print(f"网络异常: {e}")
        return []
    return all_task_ids

def download_record(task_id):
    """下载单条记录，返回 True 表示成功"""
    headers = {"Authorization": f"Bearer {TOKEN}"}
    try:
        resp = requests.get(
            f"{BASE_URL}/api/download/{task_id}",
            headers=headers,
            timeout=30
        )
        if resp.status_code == 200:
            # 保存为 task_id.json
            file_path = os.path.join(DOWNLOAD_DIR, f"{task_id}.json")
            with open(file_path, 'wb') as f:
                f.write(resp.content)
            return True
        else:
            print(f"  下载 {task_id} 失败: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"  下载 {task_id} 异常: {e}")
        return False

def main():
    print("===== 开始增量同步 =====\n")
    downloaded_set = load_progress()
    print(f"本地已有 {len(downloaded_set)} 条记录")

    all_ids = get_all_task_ids()
    if not all_ids:
        print("没有拉取到任何 task_id，退出")
        return

    # 找出新增的 ID（云端有但本地没下载过的）
    new_ids = [tid for tid in all_ids if tid not in downloaded_set]
    print(f"本次新增 {len(new_ids)} 条记录待下载\n")

    if not new_ids:
        print("没有新记录，全部已同步！")
        return

    success_count = 0
    for idx, tid in enumerate(new_ids, 1):
        print(f"[{idx}/{len(new_ids)}] 正在下载 {tid} ...")
        if download_record(tid):
            downloaded_set.add(tid)
            success_count += 1
        else:
            # 下载失败时不加入已下载集合，下次会重试
            pass

    # 更新进度文件
    save_progress(downloaded_set)
    print(f"\n同步完成：成功下载 {success_count}/{len(new_ids)} 条新记录")
    print(f"所有记录已保存到：{os.path.abspath(DOWNLOAD_DIR)}")

if __name__ == "__main__":
    main()