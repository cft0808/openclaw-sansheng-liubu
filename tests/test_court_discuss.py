"""Tests for court discuss workflow."""

import pathlib
import sys
import datetime
from types import SimpleNamespace

# Add project paths
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))

import server as srv


def _mock_round(session):
    round_no = int(session.get('rounds') or 0) + 1
    selected = session.get('participants') or []
    entries = []
    for idx, aid in enumerate(selected):
        entries.append({
            'round': round_no,
            'turn': idx + 1,
            'totalTurns': len(selected),
            'agentId': aid,
            'agentLabel': aid,
            'reply': f'{aid} 第{round_no}轮意见',
            'at': srv.now_iso(),
        })
    session.setdefault('discussion', []).extend(entries)
    session['rounds'] = round_no
    assessment = {
        'round': round_no,
        'moderatorId': selected[0] if selected else 'taizi',
        'moderatorLabel': selected[0] if selected else 'taizi',
        'recommend_stop': True,
        'reason': '信息充分',
        'question_to_emperor': '是否交由太子办理',
        'focus_next_round': [],
        'draft_direction': '按结论下旨',
        'raw': '',
        'at': srv.now_iso(),
    }
    session.setdefault('assessments', []).append(assessment)
    session['suggestedAction'] = 'finalize'
    session['status'] = 'ongoing'
    session['updatedAt'] = srv.now_iso()
    session['message'] = 'mock round done'
    return entries, assessment


def _setup_env(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_check_agent_workspace', lambda aid: True)

    def _mock_start_round(session_id):
        session = srv._load_court_session(session_id)
        if not session:
            return {'ok': False, 'error': 'session not found'}
        _mock_round(session)
        session['roundRunning'] = False
        session['currentRound'] = int(session.get('rounds') or 0)
        srv._upsert_court_session(session)
        return {'ok': True, 'message': 'mock async round done'}

    monkeypatch.setattr(srv, '_start_court_round_async', _mock_start_round)


def test_court_discuss_terminate_topic(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    started = srv.handle_court_discuss(
        action='start',
        topic='讨论调度系统是否需要新增统一提交闸门与可回放链路',
        participants=['taizi', 'zhongshu', 'menxia'],
    )
    assert started['ok'] is True
    assert started.get('sessionId')

    sid = started['sessionId']
    terminated = srv.handle_court_discuss(action='terminate', session_id=sid)
    assert terminated['ok'] is True
    assert terminated['status'] == 'terminated'
    assert terminated.get('linkedTaskId', '') == ''


def test_court_discuss_handoff_to_taizi(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    started = srv.handle_court_discuss(
        action='start',
        topic='讨论看板卡点治理是否需要执行层和提交层分离',
        participants=['taizi', 'zhongshu'],
    )
    sid = started['sessionId']

    def _mock_finalize(session, force=False):
        session['final'] = {
            'ready_for_edict': True,
            'clarified_goal': '完成调度治理',
            'risks': [],
            'questions_to_emperor': [],
            'recommended_edict': '请太子牵头推进调度治理专项改造',
            'recommended_target_dept': '中书省',
            'recommended_priority': 'high',
        }
        session['status'] = 'done'
        session['updatedAt'] = srv.now_iso()
        return {'ok': True, 'final': session['final']}

    monkeypatch.setattr(srv, '_finalize_court_session', _mock_finalize)
    monkeypatch.setattr(
        srv,
        'handle_create_task',
        lambda *args, **kwargs: {'ok': True, 'taskId': 'JJC-TEST-COURT-001'},
    )

    handoff = srv.handle_court_discuss(action='handoff', session_id=sid)
    assert handoff['ok'] is True
    assert handoff['status'] == 'handoffed'
    assert handoff['linkedTaskId'] == 'JJC-TEST-COURT-001'


def test_court_discuss_finalize_only_prepare_edict(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    started = srv.handle_court_discuss(
        action='start',
        topic='讨论拍板后自动形成旨意并交由太子督办的执行流程',
        participants=['taizi', 'zhongshu'],
    )
    sid = started['sessionId']

    def _mock_finalize(session, force=False):
        session['final'] = {
            'ready_for_edict': True,
            'clarified_goal': '先生成可编辑旨意',
            'risks': [],
            'questions_to_emperor': [],
            'recommended_edict': '请太子牵头执行本次议政结论（可先编辑）',
            'recommended_target_dept': '中书省',
            'recommended_priority': 'high',
        }
        session['status'] = 'done'
        session['updatedAt'] = srv.now_iso()
        return {'ok': True, 'final': session['final']}

    monkeypatch.setattr(srv, '_finalize_court_session', _mock_finalize)
    called = {'create_task': 0}

    def _mock_create_task(*args, **kwargs):
        called['create_task'] += 1
        return {'ok': True, 'taskId': 'JJC-TEST-COURT-002'}

    monkeypatch.setattr(srv, 'handle_create_task', _mock_create_task)

    finalized = srv.handle_court_discuss(action='finalize', session_id=sid)
    assert finalized['ok'] is True
    assert finalized['status'] == 'done'
    assert finalized.get('linkedTaskId', '') == ''
    assert finalized.get('final', {}).get('recommended_edict')
    assert called['create_task'] == 0


def test_review_approve_clears_dispatch_cooldown_and_dispatches(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = {
        'id': 'JJC-TEST-REVIEW-001',
        'title': '测试门下省准奏后应立即派发尚书省',
        'state': 'Menxia',
        'org': '门下省',
        'now': '待御批',
        'block': '无',
        'flow_log': [],
        'updatedAt': srv.now_iso(),
        '_scheduler': {
            'stateVersion': 1,
            'controlState': 'WaitingDecision',
            'lastProgressAt': srv.now_iso(),
            'cooldowns': {
                'noDispatchUntil': (
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
                ).isoformat(),
                'noEscalateUntil': (
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
                ).isoformat(),
            },
            'lease': {
                'stage': 'Menxia',
                'role': 'manual-review',
                'ownerRunId': '',
                'acquiredAt': srv.now_iso(),
                'heartbeatAt': srv.now_iso(),
                'ttlSec': 180,
            },
            'writeback': {'status': 'idle', 'retryCount': 0, 'maxRetry': 2, 'lastError': ''},
            'dispatchAttempts': 0,
            'lastDispatchStatus': '',
            'lastDispatchAgent': '',
        },
    }
    srv.save_tasks([task])

    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(
        srv.subprocess,
        'run',
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='ok', stderr=''),
    )
    monkeypatch.setattr(
        srv,
        '_bridge_apply_kanban_commands',
        lambda _task_id, _text: {'attempted': 0, 'applied': 0, 'errors': [], 'deptDispatches': []},
    )

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(srv.threading, 'Thread', _ImmediateThread)

    approved = srv.handle_review_action('JJC-TEST-REVIEW-001', 'approve', '准奏推进')
    assert approved.get('ok') is True

    latest = next(t for t in srv.load_tasks() if t.get('id') == 'JJC-TEST-REVIEW-001')
    sched = latest.get('_scheduler') or {}
    assert latest.get('state') == 'Assigned'
    assert latest.get('org') == '尚书省'
    assert sched.get('lastDispatchAgent') == 'shangshu'
    assert sched.get('lastDispatchStatus') == 'success'
    assert int(sched.get('dispatchAttempts') or 0) >= 1
    assert (sched.get('lease') or {}).get('ownerRunId', '') == ''


def test_auto_handoff_to_execution_releases_lease_and_dispatches_executor(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = {
        'id': 'JJC-TEST-HANDOFF-001',
        'title': '测试尚书省自动切执行时应立即派发工部',
        'state': 'Assigned',
        'org': '尚书省',
        'now': '待派发',
        'block': '无',
        'flow_log': [],
        'updatedAt': srv.now_iso(),
        '_scheduler': {
            'stateVersion': 1,
            'controlState': 'Assigned',
            'lastProgressAt': srv.now_iso(),
            'cooldowns': {
                'noDispatchUntil': (
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
                ).isoformat(),
                'noEscalateUntil': (
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
                ).isoformat(),
            },
            'lease': {
                'stage': 'Assigned',
                'role': 'auto-handoff',
                'ownerRunId': '',
                'acquiredAt': srv.now_iso(),
                'heartbeatAt': srv.now_iso(),
                'ttlSec': 180,
            },
            'writeback': {'status': 'idle', 'retryCount': 0, 'maxRetry': 2, 'lastError': ''},
            'dispatchAttempts': 0,
            'lastDispatchStatus': '',
            'lastDispatchAgent': '',
        },
    }
    srv.save_tasks([task])

    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(
        srv.subprocess,
        'run',
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='ok', stderr=''),
    )
    monkeypatch.setattr(
        srv,
        '_bridge_apply_kanban_commands',
        lambda _task_id, _text: {'attempted': 0, 'applied': 0, 'errors': [], 'deptDispatches': []},
    )

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(srv.threading, 'Thread', _ImmediateThread)

    out = srv._auto_handoff_to_execution('JJC-TEST-HANDOFF-001', preferred_dept='工部', trigger='unit-test')
    assert out.get('ok') is True

    latest = next(t for t in srv.load_tasks() if t.get('id') == 'JJC-TEST-HANDOFF-001')
    sched = latest.get('_scheduler') or {}
    assert latest.get('state') == 'Doing'
    assert latest.get('org') == '工部'
    assert sched.get('lastDispatchAgent') == 'gongbu'
    assert sched.get('lastDispatchStatus') == 'success'
    assert int(sched.get('dispatchAttempts') or 0) >= 1
    assert (sched.get('lease') or {}).get('ownerRunId', '') == ''


def test_court_round_degrades_on_agent_schema_error(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    session = {
        'id': 'CD-TEST-001',
        'topic': '讨论调度改造是否应先收口提交边界再扩展自动化',
        'participants': ['taizi', 'zhongshu', 'menxia'],
        'moderatorId': 'menxia',
        'status': 'ongoing',
        'rounds': 0,
        'discussion': [],
        'assessments': [],
        'emperorNotes': [{'at': srv.now_iso(), 'text': '请重点关注模型兼容风险'}],
    }

    def _mock_agent(aid, message, timeout_sec=120):
        if aid == 'menxia':
            raise RuntimeError(
                'HTTP 400: Invalid JSON payload received. Unknown name "patternProperties"'
            )
        return f'{aid} 正常发言'

    monkeypatch.setattr(srv, '_run_agent_sync', _mock_agent)

    round_entries, assessment = srv._run_court_round(session)
    assert len(round_entries) == 3
    assert any(bool(x.get('error')) for x in round_entries)
    assert assessment.get('reason')


def test_next_round_persists_emperor_note(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    started = srv.handle_court_discuss(
        action='start',
        topic='讨论任务提交闸门和执行闭环是否需要强约束',
        participants=['taizi', 'zhongshu', 'menxia'],
    )
    sid = started['sessionId']

    note = '下一轮请只保留可直接落地的方案与风险对策'
    next_round = srv.handle_court_discuss(action='next', session_id=sid, emperor_note=note)
    assert next_round['ok'] is True
    latest = srv._load_court_session(sid)
    assert latest
    assert (latest.get('emperorNotes') or [])[-1].get('text') == note


def test_finalize_builds_fallback_edict_when_too_close_to_topic(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    session = {
        'id': 'CD-TEST-FINAL',
        'topic': '请讨论并确定看板自动调度震荡治理方案',
        'participants': ['taizi', 'zhongshu', 'menxia'],
        'moderatorId': 'taizi',
        'status': 'ongoing',
        'rounds': 1,
        'discussion': [
            {
                'round': 1,
                'turn': 1,
                'totalTurns': 3,
                'agentId': 'taizi',
                'agentLabel': '太子',
                'reply': '建议优先收口状态写入入口，避免多写者竞争。',
                'at': srv.now_iso(),
            },
            {
                'round': 1,
                'turn': 2,
                'totalTurns': 3,
                'agentId': 'zhongshu',
                'agentLabel': '中书省',
                'reply': '建议将写回和执行分离，提交失败只走提交重试链路。',
                'at': srv.now_iso(),
            },
        ],
        'assessments': [
            {
                'round': 1,
                'moderatorId': 'taizi',
                'moderatorLabel': '太子',
                'recommend_stop': True,
                'reason': '讨论充分，已形成可执行方向',
                'question_to_emperor': '是否按此交办',
                'focus_next_round': [],
                'draft_direction': '围绕单写入口和提交闭环推进',
                'raw': '',
                'at': srv.now_iso(),
            }
        ],
        'emperorNotes': [{'at': srv.now_iso(), 'text': '以稳定性优先，先落地单写入口。'}],
    }

    def _mock_agent(_aid, _prompt, timeout_sec=120):
        return (
            '{"ready_for_edict": true, "clarified_goal": "完成调度治理", '
            '"risks": [], "questions_to_emperor": [], '
            '"recommended_edict": "请讨论并确定看板自动调度震荡治理方案", '
            '"recommended_target_dept": "中书省", "recommended_priority": "high"}'
        )

    monkeypatch.setattr(srv, '_run_agent_sync', _mock_agent)
    result = srv._finalize_court_session(session)
    assert result['ok'] is True
    final = result.get('final') or {}
    assert final.get('recommended_edict') != session['topic']
    assert '皇上最终拍板' in (final.get('recommended_edict') or '')


def test_run_agent_sync_routes_gemini_to_gemini_protocol(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir
    (data_dir / 'agent_config.json').write_text(
        '{"agents":[{"id":"zhongshu","model":"aihub/gemini-3-flash-preview","skills":[]}]}',
        encoding='utf-8',
    )
    called = {'gemini': 0}

    def _mock_gemini(model_name, message, timeout_sec=120):
        called['gemini'] += 1
        return 'gemini-ok'

    monkeypatch.setattr(srv, '_run_aihub_gemini_content', _mock_gemini)
    monkeypatch.setattr(srv, '_run_aihub_openai_chat', lambda *args, **kwargs: 'openai-should-not-run')

    out = srv._run_agent_sync('zhongshu', '测试消息', timeout_sec=10)
    assert out == 'gemini-ok'
    assert called['gemini'] == 1


def test_run_agent_sync_routes_non_gemini_aihub_to_openai_protocol(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir
    (data_dir / 'agent_config.json').write_text(
        '{"agents":[{"id":"taizi","model":"aihub/gpt-5.1","skills":[]}]}',
        encoding='utf-8',
    )

    monkeypatch.setattr(srv, '_run_aihub_openai_chat', lambda model_name, message, timeout_sec=120: 'openai-ok')
    out = srv._run_agent_sync('taizi', '测试消息', timeout_sec=10)
    assert out == 'openai-ok'


def test_should_auto_handoff_to_zhongshu():
    assert srv._should_auto_handoff_to_zhongshu('正在将旨意转交中书省起草执行方案，等待中书省确认')
    assert srv._should_auto_handoff_to_zhongshu('已交由中书省拟定执行方案')
    assert not srv._should_auto_handoff_to_zhongshu('仅通报进展，不转交')


def test_should_auto_handoff_to_menxia():
    assert srv._should_auto_handoff_to_menxia('中书省方案已提交门下省审议，请皇上知悉')
    assert srv._should_auto_handoff_to_menxia('请交门下省审核后再准奏')
    assert not srv._should_auto_handoff_to_menxia('仅在中书省内部讨论方案细节')


def test_commit_state_change_clears_writeback_for_non_doing():
    task = {
        'id': 'JJC-T',
        'state': 'Doing',
        'org': '中书省',
        'now': 'x',
        'block': '无',
        '_scheduler': {
            'stateVersion': 1,
            'writeback': {'status': 'ExecutionOutputReady', 'lastError': 'x'},
        },
    }
    srv._ensure_scheduler(task)
    out = srv.commit_state_change(
        task,
        action='advance',
        reason_code='test',
        to_state='Zhongshu',
        to_org='中书省',
        force=True,
    )
    assert out.get('ok') is True
    wb = task.get('_scheduler', {}).get('writeback', {})
    assert wb.get('status') == 'idle'
    assert wb.get('lastError') == ''
