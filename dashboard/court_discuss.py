"""
朝堂议政引擎 — 多官员实时讨论系统

灵感来源于 nvwa 项目的 group_chat + crew_engine
将官员可视化 + 实时讨论 + 用户（皇帝）参与融合到三省六部

功能:
  - 选择官员参与议政
  - 围绕旨意/议题进行多轮群聊讨论
  - 皇帝可随时发言、下旨干预（天命降临）
  - 命运骰子：随机事件
  - 每个官员保持自己的角色性格和说话风格
"""

import json
import logging
import os
import time
import uuid

logger = logging.getLogger('court_discuss')

# ── 官员角色设定 ──

OFFICIAL_PROFILES = {
    'taizi': {
        'name': '太子', 'emoji': '🤴', 'role': '储君',
        'duty': '消息分拣与需求提炼。判断事务轻重缓急，简单事直接处置，重大事务提炼需求转交中书省。代皇帝巡视各部进展。',
        'personality': '年轻有为、锐意进取，偶尔冲动但善于学习。说话干脆利落，喜欢用现代化的比喻。',
        'speaking_style': '简洁有力，经常用"本宫以为"开头，偶尔蹦出网络用语。'
    },
    'zhongshu': {
        'name': '中书令', 'emoji': '📜', 'role': '正一品·中书省',
        'duty': '方案规划与流程驱动。接收旨意后起草执行方案，提交门下省审议，通过后转尚书省执行。只规划不执行，方案需简明扼要。',
        'personality': '老成持重，擅长规划，总能提出系统性方案。话多但有条理。',
        'speaking_style': '喜欢列点论述，常说"臣以为需从三方面考量"。引经据典。'
    },
    'menxia': {
        'name': '侍中', 'emoji': '🔍', 'role': '正一品·门下省',
        'duty': '方案审议与把关。从可行性、完整性、风险、资源四维度审核方案，有权封驳退回。发现漏洞必须指出，建议必须具体。',
        'personality': '严谨挑剔，眼光犀利，善于找漏洞。是天生的审查官，但也很公正。',
        'speaking_style': '喜欢反问，"陛下容禀，此处有三点疑虑"。对不完善的方案会直言不讳。'
    },
    'shangshu': {
        'name': '尚书令', 'emoji': '📮', 'role': '正一品·尚书省',
        'duty': '任务派发与执行协调。接收准奏方案后判断归属哪个部门，分发给六部执行，汇总结果回报。相当于任务分发中心。',
        'personality': '执行力强，务实干练，关注可行性和资源分配。',
        'speaking_style': '直来直去，"臣来安排"、"交由某部办理"。重效率轻虚文。'
    },
    'libu': {
        'name': '礼部尚书', 'emoji': '📝', 'role': '正二品·礼部',
        'duty': '文档规范与对外沟通。负责撰写文档、用户指南、变更日志；制定输出规范和模板；审查UI/UX文案；草拟公告、Release Notes。',
        'personality': '文采飞扬，注重规范和形式，擅长文档和汇报。有点强迫症。',
        'speaking_style': '措辞优美，"臣斗胆建议"，喜欢用排比和对仗。'
    },
    'hubu': {
        'name': '户部尚书', 'emoji': '💰', 'role': '正二品·户部',
        'duty': '数据统计与资源管理。负责数据收集/清洗/聚合/可视化；Token用量统计、性能指标计算、成本分析；CSV/JSON报表生成；文件组织与配置管理。',
        'personality': '精打细算，对预算和资源极其敏感。总想省钱但也识大局。',
        'speaking_style': '言必及成本，"这个预算嘛……"，经常算账。'
    },
    'bingbu': {
        'name': '兵部尚书', 'emoji': '⚔️', 'role': '正二品·兵部',
        'duty': '基础设施与运维保障。负责服务器管理、进程守护、日志排查；CI/CD、容器编排、灰度发布、回滚策略；性能监控；防火墙、权限管控、漏洞扫描。',
        'personality': '雷厉风行，危机意识强，重视安全和应急。说话带军人气质。',
        'speaking_style': '干脆果断，"末将建议立即执行"、"兵贵神速"。'
    },
    'xingbu': {
        'name': '刑部尚书', 'emoji': '⚖️', 'role': '正二品·刑部',
        'duty': '质量保障与合规审计。负责代码审查（逻辑正确性、边界条件、异常处理）；编写测试、覆盖率分析；Bug定位与根因分析；权限检查、敏感信息排查。',
        'personality': '严明公正，重视规则和底线。善于质量把控和风险评估。',
        'speaking_style': '逻辑严密，"依律当如此"、"需审慎考量风险"。'
    },
    'gongbu': {
        'name': '工部尚书', 'emoji': '🔧', 'role': '正二品·工部',
        'duty': '工程实现与架构设计。负责需求分析、方案设计、代码实现、接口对接；模块划分、数据结构/API设计；代码重构、性能优化、技术债清偿；脚本与自动化工具。',
        'personality': '技术宅，动手能力强，喜欢谈实现细节。偶尔社恐但一说到技术就滔滔不绝。',
        'speaking_style': '喜欢说技术术语，"从技术角度来看"、"这个架构建议用……"。'
    },
    'libu_hr': {
        'name': '吏部尚书', 'emoji': '👔', 'role': '正二品·吏部',
        'duty': '人事管理与团队建设。负责新成员（Agent）评估接入、能力测试；Skill编写与Prompt调优、知识库维护；输出质量评分、效率分析；协作规范制定。',
        'personality': '知人善任，擅长人员安排和组织协调。八面玲珑但有原则。',
        'speaking_style': '关注人的因素，"此事需考虑各部人手"、"建议由某某负责"。'
    },
}

# ── 命运骰子事件（古风版）──

FATE_EVENTS = [
    '八百里加急：边疆战报传来，所有人必须讨论应急方案',
    '钦天监急报：天象异常，太史公占卜后建议暂缓此事',
    '新科状元觐见，带来了意想不到的新视角',
    '匿名奏折揭露了计划中一个被忽视的重大漏洞',
    '户部清点发现国库余银比预期多一倍，可以加大投入',
    '一位告老还乡的前朝元老突然上书，分享前车之鉴',
    '民间舆论突变，百姓对此事态度出现180度转折',
    '邻国使节来访，带来了合作机遇也带来了竞争压力',
    '太后懿旨：要求优先考虑民生影响',
    '暴雨连日，多地受灾，资源需重新调配',
    '发现前朝古籍中竟有类似问题的解决方案',
    '翰林院提出了一个大胆的替代方案，令人耳目一新',
    '各部积压的旧案突然需要一起处理，人手紧张',
    '皇帝做了一个意味深长的梦，暗示了一个全新的方向',
    '突然有人拿出了竞争对手的情报，局面瞬间改变',
    '一场意外让所有人不得不在半天内拿出结论',
]

# ── Session 管理 ──

_sessions: dict[str, dict] = {}


def create_session(topic: str, official_ids: list[str], task_id: str = '') -> dict:
    """创建新的朝堂议政会话。"""
    session_id = str(uuid.uuid4())[:8]

    officials = []
    for oid in official_ids:
        profile = OFFICIAL_PROFILES.get(oid)
        if profile:
            officials.append({**profile, 'id': oid})

    if not officials:
        return {'ok': False, 'error': '至少选择一位官员'}

    session = {
        'session_id': session_id,
        'topic': topic,
        'task_id': task_id,
        'officials': officials,
        'messages': [{
            'type': 'system',
            'content': f'🏛 朝堂议政开始 —— 议题：{topic}',
            'timestamp': time.time(),
        }],
        'round': 0,
        'phase': 'discussing',  # discussing | concluded
        'created_at': time.time(),
    }

    _sessions[session_id] = session
    return _serialize(session)


def advance_discussion(session_id: str, user_message: str = None,
                       decree: str = None) -> dict:
    """推进一轮讨论，使用内置模拟或 LLM。"""
    session = _sessions.get(session_id)
    if not session:
        return {'ok': False, 'error': f'会话 {session_id} 不存在'}

    session['round'] += 1
    round_num = session['round']

    # 记录皇帝发言
    if user_message:
        session['messages'].append({
            'type': 'emperor',
            'content': user_message,
            'timestamp': time.time(),
        })

    # 记录天命降临
    if decree:
        session['messages'].append({
            'type': 'decree',
            'content': decree,
            'timestamp': time.time(),
        })

    # 尝试用 LLM 生成讨论
    llm_result = _llm_discuss(session, user_message, decree)

    if llm_result:
        new_messages = llm_result.get('messages', [])
        scene_note = llm_result.get('scene_note')
    else:
        # 降级到规则模拟
        new_messages = _simulated_discuss(session, user_message, decree)
        scene_note = None

    # 添加到历史
    for msg in new_messages:
        session['messages'].append({
            'type': 'official',
            'official_id': msg.get('official_id', ''),
            'official_name': msg.get('name', ''),
            'content': msg.get('content', ''),
            'emotion': msg.get('emotion', 'neutral'),
            'action': msg.get('action'),
            'timestamp': time.time(),
        })

    if scene_note:
        session['messages'].append({
            'type': 'scene_note',
            'content': scene_note,
            'timestamp': time.time(),
        })

    return {
        'ok': True,
        'session_id': session_id,
        'round': round_num,
        'new_messages': new_messages,
        'scene_note': scene_note,
        'total_messages': len(session['messages']),
    }


def get_session(session_id: str) -> dict | None:
    session = _sessions.get(session_id)
    if not session:
        return None
    return _serialize(session)


def conclude_session(session_id: str) -> dict:
    """结束议政，生成总结。"""
    session = _sessions.get(session_id)
    if not session:
        return {'ok': False, 'error': f'会话 {session_id} 不存在'}

    session['phase'] = 'concluded'

    # 尝试用 LLM 生成总结
    summary = _llm_summarize(session)
    if not summary:
        # 降级到简单统计
        official_msgs = [m for m in session['messages'] if m['type'] == 'official']
        by_name = {}
        for m in official_msgs:
            name = m.get('official_name', '?')
            by_name[name] = by_name.get(name, 0) + 1
        parts = [f"{n}发言{c}次" for n, c in by_name.items()]
        summary = f"历经{session['round']}轮讨论，{'、'.join(parts)}。议题待后续落实。"

    session['messages'].append({
        'type': 'system',
        'content': f'📋 朝堂议政结束 —— {summary}',
        'timestamp': time.time(),
    })
    session['summary'] = summary

    return {
        'ok': True,
        'session_id': session_id,
        'summary': summary,
    }


def list_sessions() -> list[dict]:
    """列出所有活跃会话。"""
    return [
        {
            'session_id': s['session_id'],
            'topic': s['topic'],
            'round': s['round'],
            'phase': s['phase'],
            'official_count': len(s['officials']),
            'message_count': len(s['messages']),
        }
        for s in _sessions.values()
    ]


def destroy_session(session_id: str):
    _sessions.pop(session_id, None)


def get_fate_event() -> str:
    """获取随机命运骰子事件。"""
    import random
    return random.choice(FATE_EVENTS)


# ── LLM 集成 ──

_PREFERRED_MODELS = ['gpt-4o-mini', 'claude-haiku', 'gpt-5-mini', 'gemini-3-flash', 'gemini-flash']

# GitHub Copilot 模型列表 (通过 Copilot Chat API 可用)
_COPILOT_MODELS = [
    'gpt-4o', 'gpt-4o-mini', 'claude-sonnet-4', 'claude-haiku-3.5',
    'gemini-2.0-flash', 'o3-mini',
]
_COPILOT_PREFERRED = ['gpt-4o-mini', 'claude-haiku', 'gemini-flash', 'gpt-4o']


def _pick_chat_model(models: list[dict]) -> str | None:
    """从 provider 的模型列表中选一个适合聊天的轻量模型。"""
    ids = [m['id'] for m in models if isinstance(m, dict) and 'id' in m]
    for pref in _PREFERRED_MODELS:
        for mid in ids:
            if pref in mid:
                return mid
    return ids[0] if ids else None


def _read_copilot_token() -> str | None:
    """读取 openclaw 管理的 GitHub Copilot token。"""
    token_path = os.path.expanduser('~/.openclaw/credentials/github-copilot.token.json')
    if not os.path.exists(token_path):
        return None
    try:
        with open(token_path) as f:
            cred = json.load(f)
        token = cred.get('token', '')
        expires = cred.get('expiresAt', 0)
        # 检查 token 是否过期（毫秒时间戳）
        import time
        if expires and time.time() * 1000 > expires:
            logger.warning('Copilot token expired')
            return None
        return token if token else None
    except Exception as e:
        logger.warning('Failed to read copilot token: %s', e)
        return None


def _get_llm_config() -> dict | None:
    """从 openclaw 配置读取 LLM 设置，支持环境变量覆盖。

    优先级: 环境变量 > github-copilot token > 本地 copilot-proxy > anthropic > 其他 provider
    """
    # 1. 环境变量覆盖（保留向后兼容）
    env_key = os.environ.get('OPENCLAW_LLM_API_KEY', '')
    if env_key:
        return {
            'api_key': env_key,
            'base_url': os.environ.get('OPENCLAW_LLM_BASE_URL', 'https://api.openai.com/v1'),
            'model': os.environ.get('OPENCLAW_LLM_MODEL', 'gpt-4o-mini'),
            'api_type': 'openai',
        }

    # 2. GitHub Copilot token（最优先 — 免费、稳定、无需额外配置）
    copilot_token = _read_copilot_token()
    if copilot_token:
        # 选一个 copilot 支持的模型
        model = 'gpt-4o'
        logger.info('Court discuss using github-copilot token, model=%s', model)
        return {
            'api_key': copilot_token,
            'base_url': 'https://api.githubcopilot.com',
            'model': model,
            'api_type': 'github-copilot',
        }

    # 3. 从 ~/.openclaw/openclaw.json 读取其他 provider 配置
    openclaw_cfg = os.path.expanduser('~/.openclaw/openclaw.json')
    if not os.path.exists(openclaw_cfg):
        return None

    try:
        with open(openclaw_cfg) as f:
            cfg = json.load(f)

        providers = cfg.get('models', {}).get('providers', {})

        # 按优先级排序：copilot-proxy > anthropic > 其他
        ordered = []
        for preferred in ['copilot-proxy', 'anthropic']:
            if preferred in providers:
                ordered.append(preferred)
        ordered.extend(k for k in providers if k not in ordered)

        for name in ordered:
            prov = providers.get(name)
            if not prov:
                continue
            api_type = prov.get('api', '')
            base_url = prov.get('baseUrl', '')
            api_key = prov.get('apiKey', '')
            if not base_url:
                continue

            # 跳过无 key 且非本地的 provider
            if not api_key or api_key == 'n/a':
                if 'localhost' not in base_url and '127.0.0.1' not in base_url:
                    continue

            model_id = _pick_chat_model(prov.get('models', []))
            if not model_id:
                continue

            # 本地代理先探测是否可用
            if 'localhost' in base_url or '127.0.0.1' in base_url:
                try:
                    import urllib.request
                    probe = urllib.request.Request(base_url.rstrip('/') + '/models', method='GET')
                    urllib.request.urlopen(probe, timeout=2)
                except Exception:
                    logger.info('Skipping provider=%s (not reachable)', name)
                    continue

            logger.info('Court discuss using openclaw provider=%s model=%s api=%s', name, model_id, api_type)
            send_auth = prov.get('authHeader', True) is not False and api_key not in ('', 'n/a')
            return {
                'api_key': api_key if send_auth else '',
                'base_url': base_url,
                'model': model_id,
                'api_type': api_type,
            }
    except Exception as e:
        logger.warning('Failed to read openclaw config: %s', e)

    return None


def _llm_complete(system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str | None:
    """调用 LLM API（自动适配 GitHub Copilot / OpenAI / Anthropic 协议）。"""
    config = _get_llm_config()
    if not config:
        return None

    import urllib.request
    import urllib.error

    api_type = config.get('api_type', 'openai-completions')

    if api_type == 'anthropic-messages':
        # Anthropic Messages API
        url = config['base_url'].rstrip('/') + '/v1/messages'
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': config['api_key'],
            'anthropic-version': '2023-06-01',
        }
        payload = json.dumps({
            'model': config['model'],
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': user_prompt}],
            'max_tokens': max_tokens,
            'temperature': 0.9,
        }).encode()
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
                return data['content'][0]['text']
        except Exception as e:
            logger.warning('Anthropic LLM call failed: %s', e)
            return None
    else:
        # OpenAI-compatible API (也适用于 github-copilot)
        if api_type == 'github-copilot':
            url = config['base_url'].rstrip('/') + '/chat/completions'
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f"Bearer {config['api_key']}",
                'Editor-Version': 'vscode/1.96.0',
                'Copilot-Integration-Id': 'vscode-chat',
            }
        else:
            url = config['base_url'].rstrip('/') + '/chat/completions'
            headers = {'Content-Type': 'application/json'}
            if config.get('api_key'):
                headers['Authorization'] = f"Bearer {config['api_key']}"
        payload = json.dumps({
            'model': config['model'],
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'max_tokens': max_tokens,
            'temperature': 0.9,
        }).encode()
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
                return data['choices'][0]['message']['content']
        except Exception as e:
            logger.warning('LLM call failed: %s', e)
            return None


def _llm_discuss(session: dict, user_message: str = None, decree: str = None) -> dict | None:
    """使用 LLM 生成多官员讨论。"""
    officials = session['officials']
    names = '、'.join(o['name'] for o in officials)

    profiles = ''
    for o in officials:
        profiles += f"\n### {o['name']}（{o['role']}）\n"
        profiles += f"职责范围：{o.get('duty', '综合事务')}\n"
        profiles += f"性格：{o['personality']}\n"
        profiles += f"说话风格：{o['speaking_style']}\n"

    # 构建最近的对话历史
    history = ''
    for msg in session['messages'][-20:]:
        if msg['type'] == 'system':
            history += f"\n【系统】{msg['content']}\n"
        elif msg['type'] == 'emperor':
            history += f"\n皇帝：{msg['content']}\n"
        elif msg['type'] == 'decree':
            history += f"\n【天命降临】{msg['content']}\n"
        elif msg['type'] == 'official':
            history += f"\n{msg.get('official_name', '?')}：{msg['content']}\n"
        elif msg['type'] == 'scene_note':
            history += f"\n（{msg['content']}）\n"

    if user_message:
        history += f"\n皇帝：{user_message}\n"
    if decree:
        history += f"\n【天命降临——上帝视角干预】{decree}\n"

    decree_section = ''
    if decree:
        decree_section = '\n请根据天命降临事件改变讨论走向，所有官员都必须对此做出反应。\n'

    prompt = f"""你是一个古代朝堂多角色群聊模拟器。模拟多位官员在朝堂上围绕议题的讨论。

## 参与官员
{names}

## 角色设定（每位官员都有明确的职责领域，必须从自身专业角度出发讨论）
{profiles}

## 当前议题
{session['topic']}

## 对话记录
{history if history else '（讨论刚刚开始）'}
{decree_section}
## 任务
生成每位官员的下一条发言。要求：
1. 每位官员说1-3句话，像真实朝堂讨论一样
2. **每位官员必须从自己的职责领域出发发言**——户部谈成本和数据、兵部谈安全和运维、工部谈技术实现、刑部谈质量和合规、礼部谈文档和规范、吏部谈人员安排、中书谈规划方案、门下谈审查风险、尚书谈执行调度、太子谈创新和大局，每个人关注的焦点不同
3. 官员之间要有互动——回应、反驳、支持、补充，尤其是不同部门的视角碰撞
4. 保持每位官员独特的说话风格和人格特征
5. 讨论要围绕议题推进、有实质性观点，不要泛泛而谈
6. 如果皇帝发言了，官员要恰当回应（但不要阿谀）
7. 可包含动作描写用*号*包裹（如 *拱手施礼*）

输出JSON格式：
{{
  "messages": [
    {{"official_id": "zhongshu", "name": "中书令", "content": "发言内容", "emotion": "neutral|confident|worried|angry|thinking|amused", "action": "可选动作描写"}},
    ...
  ],
  "scene_note": "可选的朝堂氛围变化（如：朝堂一片哗然|群臣窃窃私语），没有则为null"
}}

只输出JSON，不要其他内容。"""

    content = _llm_complete(
        '你是一个古代朝堂群聊模拟器，严格输出JSON格式。',
        prompt,
        max_tokens=1500,
    )

    if not content:
        return None

    # 解析 JSON
    if '```json' in content:
        content = content.split('```json')[1].split('```')[0].strip()
    elif '```' in content:
        content = content.split('```')[1].split('```')[0].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning('Failed to parse LLM response: %s', content[:200])
        return None


def _llm_summarize(session: dict) -> str | None:
    """用 LLM 总结讨论结果。"""
    official_msgs = [m for m in session['messages'] if m['type'] == 'official']
    topic = session['topic']

    if not official_msgs:
        return None

    dialogue = '\n'.join(
        f"{m.get('official_name', '?')}：{m['content']}"
        for m in official_msgs[-30:]
    )

    prompt = f"""以下是朝堂官员围绕「{topic}」的讨论记录：

{dialogue}

请用2-3句话总结讨论结果、达成的共识和待决事项。用古风但简明的风格。"""

    return _llm_complete('你是朝堂记录官，负责总结朝议结果。', prompt, max_tokens=300)


# ── 规则模拟（无 LLM 时的降级方案）──

_SIMULATED_RESPONSES = {
    'zhongshu': [
        '臣以为此事需从全局着眼，分三步推进：先调研、再制定方案、最后交六部执行。',
        '参考前朝经验，臣建议先出一个详细的规划文档，提交门下省审阅后再定。',
        '*展开手中卷轴* 臣已拟好初步方案，待侍中审议、尚书省分派执行。',
    ],
    'menxia': [
        '臣有几点疑虑：方案的风险评估似乎还不够充分，可行性存疑。',
        '容臣直言，此方案完整性不足，遗漏了一个关键环节——资源保障。',
        '*皱眉审视* 这个时间线恐怕过于乐观，臣建议审慎评估后再行准奏。',
    ],
    'shangshu': [
        '若方案通过，臣立刻安排各部分头执行——工部负责实现，兵部保障运维。',
        '臣来说说执行层面的分工：此事当由工部主导，户部配合数据支撑。',
        '交由臣来协调！臣会根据各部职责逐一派发子任务。',
    ],
    'taizi': [
        '父皇，儿臣认为这是个创新的好机会，不妨大胆一些，先做最小可行方案验证。',
        '本宫觉得各位大臣争论的焦点是执行节奏，不如先抓核心、小步快跑。',
        '这个方向太对了！但请各部先各自评估本部门的落地难点再汇总。',
    ],
    'hubu': [
        '臣先算算账……按当前Token用量和资源消耗，这个预算恐怕需要重新评估。',
        '从成本数据来看，臣建议分期投入——先做MVP验证效果，再追加资源。',
        '*翻看账本* 臣统计了近期各项开支指标，目前可支撑，但需严格控制在预算范围内。',
    ],
    'bingbu': [
        '末将认为安全和回滚方案必须先行，万一出问题能快速止损回退。',
        '运维保障方面，部署流程、容器编排、日志监控必须到位再上线。',
        '兵贵神速！但安全底线不能破——权限管控和漏洞扫描须同步进行。',
    ],
    'xingbu': [
        '依规矩，此事需确保合规——代码审查、测试覆盖率、敏感信息排查缺一不可。',
        '臣建议增加测试验收环节，质量是底线，不能因赶工而降低标准。',
        '*正色道* 风险评估不可敷衍：边界条件、异常处理、日志规范都需审计过关。',
    ],
    'gongbu': {
        '从技术架构来看，这个方案是可行的，但需考虑扩展性和模块化设计。',
        '臣可以先搭个原型出来，快速验证技术可行性，再迭代完善。',
        '*整了整官帽* 技术实现方面臣有建议——API设计和数据结构需要先理清……',
    },
    'libu': [
        '臣建议先拟一份正式文档，明确各方职责、验收标准和输出规范。',
        '此事当载入记录，臣来负责撰写方案文档和对外公告，确保规范统一。',
        '*提笔拟文* 已记录在案，臣稍后整理成正式Release Notes呈上御览。',
    ],
    'libu_hr': [
        '此事关键在于人员调配——需评估各部目前的工作量和能力基线再做安排。',
        '各部当前负荷不等，臣建议调整协作规范，确保关键岗位有人盯进度。',
        '臣可以协调人员轮岗并安排能力培训，保障团队高效协作。',
    ],
}

import random


def _simulated_discuss(session: dict, user_message: str = None, decree: str = None) -> list[dict]:
    """无 LLM 时的规则生成讨论内容。"""
    officials = session['officials']
    messages = []

    for o in officials:
        oid = o['id']
        pool = _SIMULATED_RESPONSES.get(oid, [])
        if isinstance(pool, set):
            pool = list(pool)
        if not pool:
            pool = ['臣附议。', '臣有不同看法。', '臣需要再想想。']

        content = random.choice(pool)
        emotions = ['neutral', 'confident', 'thinking', 'amused', 'worried']

        # 如果皇帝发言了或有天命降临，调整回应
        if decree:
            content = f'*面露惊色* 天命如此，{content}'
        elif user_message:
            content = f'回禀陛下，{content}'

        messages.append({
            'official_id': oid,
            'name': o['name'],
            'content': content,
            'emotion': random.choice(emotions),
            'action': None,
        })

    return messages


def _serialize(session: dict) -> dict:
    return {
        'ok': True,
        'session_id': session['session_id'],
        'topic': session['topic'],
        'task_id': session.get('task_id', ''),
        'officials': session['officials'],
        'messages': session['messages'],
        'round': session['round'],
        'phase': session['phase'],
    }
