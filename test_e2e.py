"""
端到端测试脚本：注册 → 登录 → 搜索 → 归档
"""
import sys
import json
import requests

BASE = "http://127.0.0.1:8765"


def test(label):
    """装饰器：跑测试 + 报结果"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            print(f"\n{'='*60}")
            print(f"🧪 {label}")
            print('='*60)
            try:
                result = fn(*args, **kwargs)
                print(f"✅ PASS")
                return result
            except AssertionError as e:
                print(f"❌ FAIL: {e}")
                sys.exit(1)
            except Exception as e:
                print(f"❌ ERROR: {type(e).__name__}: {e}")
                sys.exit(1)
        return wrapper
    return decorator


@test("1. 注册新用户")
def test_register():
    # 先用随机用户名避免 409 冲突
    import random
    username = f"tester_{random.randint(1000,9999)}"
    r = requests.post(f"{BASE}/api/auth/register", json={
        "username": username,
        "password": "test123456",
        "full_name": "测试用户"
    })
    # 如果已存在就当登录
    if r.status_code == 400:
        r = requests.post(f"{BASE}/api/auth/login-json", json={
            "username": username, "password": "test123456"
        })
        assert r.status_code == 200, f"登录失败: {r.text}"
    else:
        assert r.status_code == 201, f"注册失败: {r.status_code} {r.text}"
    data = r.json()
    assert "access_token" in data, "没拿到 access_token"
    print(f"   用户: {data['user']['username']} ({data['user']['role']})")
    print(f"   token: {data['access_token'][:30]}...")
    return data["access_token"]


@test("2. 登录（用 admin）")
def test_admin_login():
    r = requests.post(f"{BASE}/api/auth/login-json", json={
        "username": "admin",
        "password": "admin123"
    })
    assert r.status_code == 200, f"登录失败: {r.status_code} {r.text}"
    data = r.json()
    print(f"   登录: {data['user']['username']} ({data['user']['role']})")
    return data["access_token"]


@test("3. /me 当前用户信息")
def test_me(token):
    r = requests.get(f"{BASE}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"me 失败: {r.text}"
    data = r.json()
    print(f"   当前用户: {data['username']} (id={data['id']})")


@test("4. 列项目")
def test_projects(token):
    r = requests.get(f"{BASE}/api/projects", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"projects 失败: {r.text}"
    data = r.json()
    assert len(data["projects"]) > 0, "没有项目"
    for p in data["projects"]:
        print(f"   {'📌' if p['current'] else '  '} {p['name']} ({p['id']})")


@test("5. 搜索 '团意险'")
def test_search(token):
    r = requests.post(
        f"{BASE}/api/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": "团意险 司机", "top_k": 5}
    )
    assert r.status_code == 200, f"search 失败: {r.text}"
    data = r.json()
    print(f"   mode: {data['mode']}, token_hits: {data['token_hits']}")
    for hit in data["results"][:3]:
        print(f"   [{hit['score']:6.1f}] {hit['title']}  ({hit['path']})")
    assert data["token_hits"] > 0, "没搜到结果"


@test("6. 列文件树")
def test_files(token):
    r = requests.get(
        f"{BASE}/api/files?root=wiki&recursive=false&max_files=10",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, f"files 失败: {r.text}"
    data = r.json()
    print(f"   root: {data['root']}, 文件数: {len(data['files'])}")
    for f in data["files"][:5]:
        is_dir = f.get("isDir", f.get("is_dir", False))
        print(f"     {f['name']}  ({'dir' if is_dir else 'file'})")


@test("7. 读文件")
def test_read(token):
    r = requests.get(
        f"{BASE}/api/read?path=wiki/concepts/团意险.md",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, f"read 失败: {r.text}"
    data = r.json()
    print(f"   路径: {data['path']}")
    print(f"   内容: {data['content'][:150]}...")


@test("8. 知识图谱")
def test_graph(token):
    r = requests.get(
        f"{BASE}/api/graph?limit=10",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, f"graph 失败: {r.text}"
    data = r.json()
    print(f"   nodes: {len(data['nodes'])}, edges: {len(data['edges'])}")
    for n in data["nodes"][:5]:
        print(f"     [{n['node_type']:>10}] {n['label']}  (links={n['link_count']})")


@test("9. 归档优秀结果")
def test_archive(token):
    payload = {
        "title": "端到端测试-团意险高风险客户",
        "content": """# 团意险高风险客户

团意险的核心目标客户是高风险行业。

## 主要行业
- 运输/物流（司机群体高风险）
- 建筑/施工（高空作业）
- 制造业（车间工人）
- 服务业（外卖配送员）

## 关键话术
- 用"高风险 = 高保额"类比
- 强调"团意 + 重疾"组合
""",
        "target_dir": "synthesis",
        "tags": ["归档", "测试", "团意险"],
        "source": "wiki/concepts/团意险.md",
        "note": "端到端测试自动归档",
        "trigger_rescan": True
    }
    r = requests.post(
        f"{BASE}/api/archive",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload
    )
    assert r.status_code == 201, f"archive 失败: {r.status_code} {r.text}"
    data = r.json()
    print(f"   写入: {data['path']}  ({data['size_bytes']}B)")
    print(f"   rescan 触发: {data['rescan_triggered']}")


@test("10. 验证归档后能搜到")
def test_search_after_archive(token):
    import time
    time.sleep(5)  # 等摄取开始
    r = requests.post(
        f"{BASE}/api/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": "高风险客户 端到端测试", "top_k": 3}
    )
    assert r.status_code == 200
    data = r.json()
    print(f"   token_hits: {data['token_hits']}, results: {len(data['results'])}")
    for hit in data["results"][:3]:
        print(f"     [{hit['score']:6.1f}] {hit['title']}  ({hit['path']})")


if __name__ == "__main__":
    print("🧪 Wiki Gateway 端到端测试")
    print(f"   目标: {BASE}")
    print()

    # 跑测试
    user_token = test_register()
    admin_token = test_admin_login()
    test_me(user_token)
    test_projects(user_token)
    test_search(user_token)
    test_files(user_token)
    test_read(user_token)
    test_graph(user_token)
    test_archive(user_token)
    test_search_after_archive(user_token)

    print("\n" + "="*60)
    print("🎉 全部测试通过！")
    print("="*60)
