#!/usr/bin/env python3
"""
三省六部 · Skill 管理工具
支持从本地或远程 URL 添加、更新、查看和移除 skills

Usage:
  python3 scripts/skill_manager.py add-remote --agent zhongshu --name code_review \\
    --source https://raw.githubusercontent.com/org/skills/main/code_review/SKILL.md \\
    --description "代码审查"
  
  python3 scripts/skill_manager.py list-remote
  
  python3 scripts/skill_manager.py update-remote --agent zhongshu --name code_review
  
  python3 scripts/skill_manager.py remove-remote --agent zhongshu --name code_review
  
  python3 scripts/skill_manager.py import-official-hub --agents zhongshu,menxia,shangshu
"""
import sys
import json
import pathlib
import argparse
import os
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import now_iso, safe_name, read_json

OCLAW_HOME = Path.home() / '.openclaw'


def _download_file(url: str, timeout: int = 30, retries: int = 3) -> str:
    """从 URL 下载文件内容（文本格式），支持重试"""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read(10 * 1024 * 1024)  # 最多 10MB
                return content.decode('utf-8')
        except urllib.error.HTTPError as e:
            last_error = f'HTTP {e.code}: {e.reason}'
            if e.code in (404, 403):
                break  # 不重试 4xx
        except urllib.error.URLError as e:
            last_error = f'网络错误: {e.reason}'
        except Exception as e:
            last_error = f'{type(e).__name__}: {e}'
        
        if attempt < retries:
            import time
            wait = attempt * 3  # 3s, 6s
            print(f'   ⚠️ 第 {attempt} 次下载失败({last_error})，{wait}秒后重试...')
            time.sleep(wait)
    
    # 所有重试失败
    hint = ''
    if 'timed out' in str(last_error).lower() or '超时' in str(last_error):
        hint = '\n   💡 提示: 如果在中国大陆，请设置代理 export https_proxy=http://proxy:port'
    elif '404' in str(last_error):
        hint = '\n   💡 提示: 官方 Skills Hub 可能尚未发布该 skill，请检查 URL 是否正确'
    raise Exception(f'{last_error} (已重试 {retries} 次){hint}')


def _compute_checksum(content: str) -> str:
    """计算内容的简单校验和"""
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def add_remote(agent_id: str, name: str, source_url: str, description: str = '') -> bool:
    """从远程 URL 为 Agent 添加 skill"""
    if not safe_name(agent_id) or not safe_name(name):
        print(f'❌ 错误：agent_id 或 skill 名称含非法字符')
        return False
    
    # 设置 workspace
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    
    # 下载文件
    print(f'⏳ 正在从 {source_url} 下载...')
    try:
        content = _download_file(source_url)
    except Exception as e:
        print(f'❌ 下载失败：{e}')
        print(f'   URL: {source_url}')
        return False
    
    # 基础验证（放宽检查：有些 skill 不以 --- 开头）
    if len(content.strip()) < 10:
        print(f'❌ 文件内容过短或为空')
        return False
    
    # 保存 SKILL.md
    skill_md.write_text(content)
    
    # 保存源信息
    source_info = {
        'skillName': name,
        'sourceUrl': source_url,
        'description': description,
        'addedAt': now_iso(),
        'lastUpdated': now_iso(),
        'checksum': _compute_checksum(content),
        'status': 'valid',
    }
    source_json = workspace / '.source.json'
    source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))
    
    print(f'✅ 技能 {name} 已添加到 {agent_id}')
    print(f'   路径: {skill_md}')
    print(f'   大小: {len(content)} 字节')
    return True


def list_remote() -> bool:
    """列出所有已添加的远程 skills"""
    if not OCLAW_HOME.exists():
        print('❌ OCLAW_HOME 不存在')
        return False
    
    remote_skills = []
    
    for ws_dir in OCLAW_HOME.glob('workspace-*'):
        agent_id = ws_dir.name.replace('workspace-', '')
        skills_dir = ws_dir / 'skills'
        if not skills_dir.exists():
            continue
        
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            source_json = skill_dir / '.source.json'
            
            if not source_json.exists():
                continue
            
            try:
                source_info = json.loads(source_json.read_text())
                remote_skills.append({
                    'agent': agent_id,
                    'skill': skill_name,
                    'source': source_info.get('sourceUrl', 'N/A'),
                    'desc': source_info.get('description', ''),
                    'added': source_info.get('addedAt', 'N/A'),
                })
            except Exception:
                pass
    
    if not remote_skills:
        print('📭 暂无远程 skills')
        return True
    
    print(f'📋 共 {len(remote_skills)} 个远程 skills：\n')
    print(f'{"Agent":<12} | {"Skill 名称":<20} | {"描述":<30} | 添加时间')
    print('-' * 100)
    
    for sk in remote_skills:
        desc = (sk['desc'] or sk['source'])[:30].ljust(30)
        print(f"{sk['agent']:<12} | {sk['skill']:<20} | {desc} | {sk['added'][:10]}")
    
    print()
    return True


def update_remote(agent_id: str, name: str) -> bool:
    """更新远程 skill 为最新版本"""
    if not safe_name(agent_id) or not safe_name(name):
        print(f'❌ 错误：agent_id 或 skill 名称含非法字符')
        return False
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / name
    source_json = workspace / '.source.json'
    
    if not source_json.exists():
        print(f'❌ 技能不存在或不是远程 skill: {name}')
        return False
    
    try:
        source_info = json.loads(source_json.read_text())
        source_url = source_info.get('sourceUrl')
        if not source_url:
            print(f'❌ 无效的源 URL')
            return False
        
        # 重新下载
        return add_remote(agent_id, name, source_url, source_info.get('description', ''))
    except Exception as e:
        print(f'❌ 更新失败：{e}')
        return False


def remove_remote(agent_id: str, name: str) -> bool:
    """移除远程 skill"""
    if not safe_name(agent_id) or not safe_name(name):
        print(f'❌ 错误：agent_id 或 skill 名称含非法字符')
        return False
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / name
    source_json = workspace / '.source.json'
    
    if not source_json.exists():
        print(f'❌ 技能不存在或不是远程 skill: {name}')
        return False
    
    try:
        import shutil
        shutil.rmtree(workspace)
        print(f'✅ 技能 {name} 已从 {agent_id} 移除')
        return True
    except Exception as e:
        print(f'❌ 移除失败：{e}')
        return False


OFFICIAL_SKILLS_HUB_BASE = 'https://raw.githubusercontent.com/openclaw-ai/skills-hub/main'
# 备用镜像（GitHub 国内访问不稳定时自动切换）
_FALLBACK_HUB_BASES = [
    'https://ghproxy.com/https://raw.githubusercontent.com/openclaw-ai/skills-hub/main',
    'https://raw.gitmirror.com/openclaw-ai/skills-hub/main',
]

# 支持通过环境变量覆盖 Hub 地址
_HUB_BASE_ENV = 'OPENCLAW_SKILLS_HUB_BASE'

def _get_hub_url(skill_name):
    """获取 skill 的 Hub URL，支持环境变量覆盖"""
    base = (Path.home() / '.openclaw' / 'skills-hub-url').read_text().strip() \
        if (Path.home() / '.openclaw' / 'skills-hub-url').exists() else None
    base = base or os.environ.get(_HUB_BASE_ENV) or OFFICIAL_SKILLS_HUB_BASE
    return f'{base.rstrip("/")}/{skill_name}/SKILL.md'


OFFICIAL_SKILLS_HUB = {
    'code_review': _get_hub_url('code_review'),
    'api_design': _get_hub_url('api_design'),
    'security_audit': _get_hub_url('security_audit'),
    'data_analysis': _get_hub_url('data_analysis'),
    'doc_generation': _get_hub_url('doc_generation'),
    'test_framework': _get_hub_url('test_framework'),
}

SKILL_AGENT_MAPPING = {
    'code_review': ('bingbu', 'xingbu', 'menxia'),
    'api_design': ('bingbu', 'gongbu', 'menxia'),
    'security_audit': ('xingbu', 'menxia'),
    'data_analysis': ('hubu', 'menxia'),
    'doc_generation': ('libu', 'menxia'),
    'test_framework': ('gongbu', 'xingbu', 'menxia'),
}


def import_official_hub(agent_ids: list) -> bool:
    """从官方 Skills Hub 导入指定的 skills 到指定 agents。
    如果未指定 agents，使用该 skill 的推荐 agents。
    """
    if not agent_ids:
        print('❌ 未指定 agent，使用推荐配置...\n')
        for skill_name, recommended_agents in SKILL_AGENT_MAPPING.items():
            agent_ids.extend(recommended_agents)
        agent_ids = list(set(agent_ids))
    
    total = 0
    success = 0
    failed = []
    
    for skill_name, url in OFFICIAL_SKILLS_HUB.items():
        # 确定目标 agents
        target_agents = agent_ids
        if not agent_ids:
            target_agents = SKILL_AGENT_MAPPING.get(skill_name, ['menxia'])
        
        print(f'\n📥 正在导入 skill: {skill_name}')
        print(f'   目标 agents: {", ".join(target_agents)}')
        
        # 尝试主 URL，失败则自动切换镜像
        effective_url = url
        for agent_id in target_agents:
            total += 1
            ok = add_remote(agent_id, skill_name, effective_url, f'官方 skill：{skill_name}')
            if not ok and effective_url == url:
                # 主 URL 失败，尝试镜像
                for fb_base in _FALLBACK_HUB_BASES:
                    fb_url = f'{fb_base.rstrip("/")}/{skill_name}/SKILL.md'
                    print(f'   🔄 尝试镜像: {fb_url}')
                    ok = add_remote(agent_id, skill_name, fb_url, f'官方 skill：{skill_name}')
                    if ok:
                        effective_url = fb_url  # 后续 agent 也用这个镜像
                        break
            if ok:
                success += 1
            else:
                failed.append(f'{agent_id}/{skill_name}')
    
    print(f'\n📊 导入完成：{success}/{total} 个 skills 成功')
    if failed:
        print(f'\n❌ 失败列表:')
        for f in failed:
            print(f'   - {f}')
        print(f'\n💡 排查建议:')
        print(f'   1. 检查网络: curl -I {OFFICIAL_SKILLS_HUB_BASE}/code_review/SKILL.md')
        print(f'   2. 设置代理: export https_proxy=http://your-proxy:port')
        print(f'   3. 使用镜像: export {_HUB_BASE_ENV}=https://ghproxy.com/{OFFICIAL_SKILLS_HUB_BASE}')
        print(f'   4. 自定义源: echo "https://your-mirror/skills" > ~/.openclaw/skills-hub-url')
        print(f'   5. 单独重试: python3 scripts/skill_manager.py add-remote --agent <agent> --name <skill> --source <url>')
    return success == total


def main():
    parser = argparse.ArgumentParser(description='三省六部 Skill 管理工具', 
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest='cmd', help='命令')
    
    # add-remote
    add_parser = subparsers.add_parser('add-remote', help='从远程 URL 添加 skill')
    add_parser.add_argument('--agent', required=True, help='目标 Agent ID')
    add_parser.add_argument('--name', required=True, help='Skill 内部名称')
    add_parser.add_argument('--source', required=True, help='远程 URL 或本地路径')
    add_parser.add_argument('--description', default='', help='Skill 描述')
    
    # list-remote
    subparsers.add_parser('list-remote', help='列出所有远程 skills')
    
    # update-remote
    update_parser = subparsers.add_parser('update-remote', help='更新远程 skill')
    update_parser.add_argument('--agent', required=True, help='Agent ID')
    update_parser.add_argument('--name', required=True, help='Skill 名称')
    
    # remove-remote
    remove_parser = subparsers.add_parser('remove-remote', help='移除远程 skill')
    remove_parser.add_argument('--agent', required=True, help='Agent ID')
    remove_parser.add_argument('--name', required=True, help='Skill 名称')
    
    # import-official-hub
    import_parser = subparsers.add_parser('import-official-hub', help='从官方库导入 skills')
    import_parser.add_argument('--agents', default='', help='逗号分隔的 Agent IDs（可选）')
    
    # check-updates
    check_parser = subparsers.add_parser('check-updates', help='检查更新（未来功能）')
    check_parser.add_argument('--interval', default='weekly', 
                             help='检查间隔 (weekly/daily/monthly)')
    
    args = parser.parse_args()
    
    if not args.cmd:
        parser.print_help()
        return
    
    if args.cmd == 'add-remote':
        success = add_remote(args.agent, args.name, args.source, args.description)
        sys.exit(0 if success else 1)
    
    elif args.cmd == 'list-remote':
        success = list_remote()
        sys.exit(0 if success else 1)
    
    elif args.cmd == 'update-remote':
        success = update_remote(args.agent, args.name)
        sys.exit(0 if success else 1)
    
    elif args.cmd == 'remove-remote':
        success = remove_remote(args.agent, args.name)
        sys.exit(0 if success else 1)
    
    elif args.cmd == 'import-official-hub':
        agent_list = [a.strip() for a in args.agents.split(',') if a.strip()] if args.agents else []
        success = import_official_hub(agent_list)
        sys.exit(0 if success else 1)
    
    elif args.cmd == 'check-updates':
        print(f'⏳ 检查更新功能（间隔: {args.interval}）尚未实现')
        print(f'   敬请期待...')


if __name__ == '__main__':
    main()
