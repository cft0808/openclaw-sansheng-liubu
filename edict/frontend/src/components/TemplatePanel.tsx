import { useEffect, useState } from 'react';
import { useStore, TEMPLATES, TPL_CATS } from '../store';
import type { Template } from '../store';
import { api, type CourtDiscussResult } from '../api';

const FREE_TARGET_DEPTS = ['中书省', '尚书省', '礼部', '户部', '兵部', '刑部', '工部', '吏部'];
const DISCUSS_AGENT_OPTIONS = [
  { id: 'taizi', label: '太子', emoji: '🤴' },
  { id: 'zhongshu', label: '中书省', emoji: '📜' },
  { id: 'menxia', label: '门下省', emoji: '🔍' },
  { id: 'shangshu', label: '尚书省', emoji: '📮' },
  { id: 'hubu', label: '户部', emoji: '💰' },
  { id: 'bingbu', label: '兵部', emoji: '⚔️' },
];

export default function TemplatePanel() {
  const tplCatFilter = useStore((s) => s.tplCatFilter);
  const setTplCatFilter = useStore((s) => s.setTplCatFilter);
  const toast = useStore((s) => s.toast);
  const loadAll = useStore((s) => s.loadAll);

  const [formTpl, setFormTpl] = useState<Template | null>(null);
  const [formVals, setFormVals] = useState<Record<string, string>>({});
  const [previewCmd, setPreviewCmd] = useState('');
  const [freeTitle, setFreeTitle] = useState('');
  const [freeTargetDept, setFreeTargetDept] = useState('');
  const [freePriority, setFreePriority] = useState('normal');
  const [discussTopic, setDiscussTopic] = useState('');
  const [discussParticipants, setDiscussParticipants] = useState<string[]>(['taizi', 'zhongshu', 'menxia']);
  const [discussSessionId, setDiscussSessionId] = useState('');
  const [discussLoading, setDiscussLoading] = useState(false);
  const [discussResult, setDiscussResult] = useState<CourtDiscussResult | null>(null);
  const [discussWindowOpen, setDiscussWindowOpen] = useState(false);
  const [emperorNote, setEmperorNote] = useState('');

  useEffect(() => {
    if (!discussWindowOpen || !discussSessionId) return;
    const timer = setInterval(async () => {
      try {
        const r = await api.courtDiscuss({ action: 'status', sessionId: discussSessionId });
        setDiscussResult(r);
      } catch {
        /* ignore polling errors */
      }
    }, 1200);
    return () => clearInterval(timer);
  }, [discussWindowOpen, discussSessionId]);

  let tpls = TEMPLATES;
  if (tplCatFilter !== '全部') tpls = tpls.filter((t) => t.cat === tplCatFilter);

  const openForm = (tpl: Template) => {
    const vals: Record<string, string> = {};
    tpl.params.forEach((p) => {
      vals[p.key] = p.default || '';
    });
    setFormVals(vals);
    setFormTpl(tpl);
    setPreviewCmd('');
  };

  const buildCmd = (tpl: Template) => {
    let cmd = tpl.command;
    for (const p of tpl.params) {
      cmd = cmd.replace(new RegExp('\\{' + p.key + '\\}', 'g'), formVals[p.key] || p.default || '');
    }
    return cmd;
  };

  const preview = () => {
    if (!formTpl) return;
    setPreviewCmd(buildCmd(formTpl));
  };

  const ensureGatewayReady = async () => {
    try {
      const st = await api.agentsStatus();
      if (st.ok && st.gateway && !st.gateway.alive) {
        toast('⚠️ Gateway 未启动，任务将无法派发！', 'err');
        if (!confirm('Gateway 未启动，继续？')) return false;
      }
    } catch {
      /* ignore */
    }
    return true;
  };

  const execute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formTpl) return;
    const cmd = buildCmd(formTpl);
    if (!cmd.trim()) {
      toast('请填写必填参数', 'err');
      return;
    }

    if (!(await ensureGatewayReady())) return;

    if (!confirm(`确认下旨？\n\n${cmd.substring(0, 200)}${cmd.length > 200 ? '…' : ''}`)) return;

    try {
      const params: Record<string, string> = {};
      for (const p of formTpl.params) {
        params[p.key] = formVals[p.key] || p.default || '';
      }
      const r = await api.createTask({
        title: cmd,
        org: '中书省',
        targetDept: formTpl.depts[0] || '',
        priority: 'normal',
        templateId: formTpl.id,
        params,
      });
      if (r.ok) {
        toast(`📜 ${r.taskId} 旨意已下达`, 'ok');
        setFormTpl(null);
        loadAll();
      } else {
        toast(r.error || '下旨失败', 'err');
      }
    } catch {
      toast('⚠️ 服务器连接失败', 'err');
    }
  };

  const toggleDiscussParticipant = (agentId: string) => {
    setDiscussParticipants((curr) => {
      if (curr.includes(agentId)) {
        if (curr.length <= 1) return curr;
        return curr.filter((x) => x !== agentId);
      }
      return [...curr, agentId];
    });
  };

  const startCourtDiscuss = async (e: React.FormEvent) => {
    e.preventDefault();
    const topic = discussTopic.trim();
    if (topic.length < 10) {
      toast('议题至少 10 个字', 'err');
      return;
    }
    if (discussParticipants.length < 2) {
      toast('至少选择 2 位大臣参与议政', 'err');
      return;
    }
    if (!(await ensureGatewayReady())) return;
    if (!confirm(`开始御前议政？\n\n议题：${topic.substring(0, 120)}${topic.length > 120 ? '…' : ''}`)) return;
    setDiscussWindowOpen(true);
    setDiscussLoading(true);
    setDiscussSessionId('');
    setDiscussResult(null);
    try {
      const r = await api.courtDiscuss({
        action: 'start',
        topic,
        participants: discussParticipants,
        emperorNote: emperorNote.trim(),
      });
      setDiscussResult(r);
      setDiscussSessionId(r.sessionId || '');
      if (r.ok) {
        toast('🧠 首轮议政已启动，正在轮番发言', 'ok');
      } else {
        toast(r.error || '议政失败', 'err');
      }
    } catch {
      toast('⚠️ 服务器连接失败', 'err');
    } finally {
      setDiscussLoading(false);
    }
  };

  const refreshCourtDiscussStatus = async () => {
    if (!discussSessionId) return;
    try {
      const r = await api.courtDiscuss({
        action: 'status',
        sessionId: discussSessionId,
      });
      setDiscussResult(r);
    } catch {
      /* ignore */
    }
  };

  const continueCourtDiscuss = async () => {
    if (!discussSessionId) {
      toast('请先开始议政', 'err');
      return;
    }
    setDiscussLoading(true);
    try {
      const r = await api.courtDiscuss({
        action: 'next',
        sessionId: discussSessionId,
        emperorNote: emperorNote.trim(),
      });
      setDiscussResult(r);
      if (r.ok) {
        toast('已启动下一轮讨论', 'ok');
      } else {
        toast(r.error || '继续讨论失败', 'err');
      }
    } catch {
      toast('⚠️ 服务器连接失败', 'err');
    } finally {
      setDiscussLoading(false);
    }
  };

  const finalizeCourtDiscuss = async () => {
    if (!discussSessionId) {
      toast('请先开始议政', 'err');
      return;
    }
    if (!confirm('确认皇上拍板结束，并将综合旨意复制到自由下旨区？')) return;
    setDiscussLoading(true);
    try {
      const r = await api.courtDiscuss({
        action: 'finalize',
        sessionId: discussSessionId,
        emperorNote: emperorNote.trim(),
      });
      setDiscussResult(r);
      if (r.ok) {
        const edict = buildDiscussConclusionForFreeEdict(r);
        if (edict) {
          setFreeTitle(edict);
        }
        if (r.final?.recommended_target_dept) {
          setFreeTargetDept(r.final.recommended_target_dept);
        }
        if (r.final?.recommended_priority) {
          setFreePriority(r.final.recommended_priority);
        }
        setDiscussWindowOpen(false);
        setEmperorNote('');
        toast('✅ 已形成综合旨意，已复制到自由下旨区，请修改后下达', 'ok');
      } else {
        toast(r.error || '拍板失败', 'err');
      }
    } catch {
      toast('⚠️ 服务器连接失败', 'err');
    } finally {
      setDiscussLoading(false);
    }
  };

  const normalizeForCompare = (text: string) => text.replace(/[^\p{L}\p{N}]+/gu, '').toLowerCase();

  const isTooCloseToTopic = (topic: string, edict: string) => {
    const t = normalizeForCompare(topic);
    const e = normalizeForCompare(edict);
    if (!e || edict.trim().length < 20) return true;
    if (!t) return false;
    if (t === e || t.includes(e) || e.includes(t)) return true;
    let same = 0;
    for (const ch of e) {
      if (t.includes(ch)) same += 1;
    }
    return same / Math.max(e.length, 1) > 0.85;
  };

  const buildDiscussConclusionForFreeEdict = (r: CourtDiscussResult) => {
    const topic = (r.topic || '').trim();
    const final = r.final;
    const edict = (final?.recommended_edict || '').trim();
    const clarifiedGoal = (final?.clarified_goal || '').trim();
    const draftDirection = (r.assessment?.draft_direction || '').trim();
    const latestNote = (r.emperorNotes || []).slice(-1)[0]?.text?.trim() || '';

    if (edict && !isTooCloseToTopic(topic, edict)) return edict;

    const round = r.rounds || 0;
    const highlights = (r.discussion || [])
      .filter((x) => (x.round || 0) === round && (x.reply || '').trim())
      .slice(-3)
      .map((x) => {
        const plain = (x.reply || '').replace(/\s+/g, ' ').trim();
        const short = plain.length > 90 ? `${plain.slice(0, 90)}...` : plain;
        return `${x.agentLabel}：${short}`;
      });

    const goal = clarifiedGoal || draftDirection || topic || '落实本次议政结论';
    const lines = [`请太子牵头办理：${goal}。`];
    if (highlights.length > 0) lines.push(`议政要点：${highlights.join('；')}。`);
    if (latestNote) lines.push(`皇上最终拍板：${latestNote}。`);
    lines.push('请形成执行方案、风险清单与里程碑，并按节点回报。');
    return lines.join('\n');
  };

  const adoptDiscussEdict = () => {
    if (!discussResult) {
      toast('没有可用的议政结论', 'err');
      return;
    }
    const edict = buildDiscussConclusionForFreeEdict(discussResult);
    if (!edict) {
      toast('没有可用的建议旨意', 'err');
      return;
    }
    setFreeTitle(edict);
    if (discussResult?.final?.recommended_target_dept) {
      setFreeTargetDept(discussResult.final.recommended_target_dept);
    }
    if (discussResult?.final?.recommended_priority) {
      setFreePriority(discussResult.final.recommended_priority);
    }
    setDiscussWindowOpen(false);
    toast('已将议政结论填入自由下旨区', 'ok');
  };

  const terminateCourtDiscuss = async () => {
    if (!discussSessionId) {
      toast('请先开始议政', 'err');
      return;
    }
    if (!confirm('确认终止该话题，不进入办理流程？')) return;
    setDiscussLoading(true);
    try {
      const r = await api.courtDiscuss({
        action: 'terminate',
        sessionId: discussSessionId,
        emperorNote: emperorNote.trim(),
      });
      setDiscussResult(r);
      if (r.ok) {
        toast('🛑 话题已终止', 'ok');
      } else {
        toast(r.error || '终止失败', 'err');
      }
    } catch {
      toast('⚠️ 服务器连接失败', 'err');
    } finally {
      setDiscussLoading(false);
    }
  };

  const submitFreeEdict = async (e: React.FormEvent) => {
    e.preventDefault();
    const title = freeTitle.trim();
    if (!title) {
      toast('请先输入旨意内容', 'err');
      return;
    }
    if (title.length < 10) {
      toast('旨意至少 10 个字，避免被判定为闲聊', 'err');
      return;
    }
    if (!(await ensureGatewayReady())) return;
    if (!confirm(`确认下旨给太子？\n\n${title.substring(0, 200)}${title.length > 200 ? '…' : ''}`)) return;

    try {
      const r = await api.createTask({
        title,
        org: '中书省',
        targetDept: freeTargetDept || '',
        priority: freePriority,
        templateId: 'manual-free',
        params: { source: 'free-edict' },
      });
      if (r.ok) {
        toast(`📜 ${r.taskId} 旨意已下达`, 'ok');
        setFreeTitle('');
        setFreeTargetDept('');
        setFreePriority('normal');
        loadAll();
      } else {
        toast(r.error || '下旨失败', 'err');
      }
    } catch {
      toast('⚠️ 服务器连接失败', 'err');
    }
  };

  return (
    <div>
      <div
        style={{
          background: 'var(--panel)',
          border: '1px solid var(--line)',
          borderRadius: 12,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>御前议政（先讨论再下旨）</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
          适合想法尚未成熟时，先让太子与诸臣讨论澄清，再生成可执行旨意。
        </div>
        <form onSubmit={startCourtDiscuss}>
          <textarea
            className="tpl-input"
            style={{ minHeight: 88, resize: 'vertical', marginBottom: 10 }}
            placeholder="例如：目前天下要闻标题是英文，想在看板中展示中文标题，但点击后保持英文原文。请先讨论清楚方案和边界。"
            value={discussTopic}
            onChange={(e) => setDiscussTopic(e.target.value)}
            disabled={discussLoading}
          />
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
            <button type="submit" className="tpl-go" style={{ flex: '0 0 170px' }} disabled={discussLoading}>
              {discussLoading ? '⏳ 议政中...' : '🧠 开始首轮议政'}
            </button>
            {discussSessionId && (
              <span style={{ fontSize: 11, color: 'var(--muted)', alignSelf: 'center' }}>
                会话: {discussSessionId}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
            {DISCUSS_AGENT_OPTIONS.map((x) => {
              const active = discussParticipants.includes(x.id);
              return (
                <button
                  key={x.id}
                  type="button"
                  className="tpl-cat"
                  onClick={() => toggleDiscussParticipant(x.id)}
                  style={{
                    cursor: 'pointer',
                    borderColor: active ? 'var(--acc)' : 'var(--line)',
                    color: active ? 'var(--text)' : 'var(--muted)',
                    background: active ? 'var(--acc-soft)' : 'transparent',
                  }}
                  disabled={discussLoading}
                >
                  {x.emoji} {x.label}
                </button>
              );
            })}
          </div>
        </form>

        {discussResult && (
          <div
            style={{
              borderTop: '1px dashed var(--line)',
              marginTop: 10,
              paddingTop: 10,
              fontSize: 12,
            }}
          >
            <div style={{ marginBottom: 8 }}>
              当前状态：
              <strong style={{ marginLeft: 6 }}>
                {discussResult.status === 'handoffed'
                  ? '已交办太子'
                  : discussResult.status === 'terminated'
                  ? '已终止'
                  : discussResult.status === 'done'
                  ? '讨论已结束'
                  : '讨论进行中'}
              </strong>
              {typeof discussResult.rounds === 'number' && <span style={{ marginLeft: 10 }}>轮次：{discussResult.rounds}</span>}
            </div>
            {discussResult.message && <div style={{ color: 'var(--muted)', marginBottom: 8 }}>{discussResult.message}</div>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                type="button"
                className="btn btn-g"
                onClick={() => {
                  setDiscussWindowOpen(true);
                  refreshCourtDiscussStatus();
                }}
              >
                🪟 打开议政窗口
              </button>
              <button type="button" className="tpl-go" onClick={refreshCourtDiscussStatus}>
                刷新状态
              </button>
            </div>
          </div>
        )}
      </div>

      <div
        style={{
          background: 'var(--panel)',
          border: '1px solid var(--line)',
          borderRadius: 12,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>自由下旨（不走模板）</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
          直接输入任务指令，系统会创建旨意并先派发给太子分拣。
        </div>
        <form onSubmit={submitFreeEdict}>
          <textarea
            className="tpl-input"
            style={{ minHeight: 92, resize: 'vertical', marginBottom: 10 }}
            placeholder="例如：请对 edict 项目做一次端到端测试审查，重点检查任务卡住时的自动重试与回滚链路。"
            value={freeTitle}
            onChange={(e) => setFreeTitle(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
            <select
              className="tpl-input"
              style={{ flex: '1 1 220px' }}
              value={freeTargetDept}
              onChange={(e) => setFreeTargetDept(e.target.value)}
            >
              <option value="">建议执行部门（可选）</option>
              {FREE_TARGET_DEPTS.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <select
              className="tpl-input"
              style={{ flex: '0 0 160px' }}
              value={freePriority}
              onChange={(e) => setFreePriority(e.target.value)}
            >
              <option value="low">低</option>
              <option value="normal">普通</option>
              <option value="high">高</option>
              <option value="critical">紧急</option>
            </select>
            <button type="submit" className="tpl-go" style={{ flex: '0 0 130px' }}>📜 下旨</button>
          </div>
        </form>
      </div>

      {/* Category filter */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        {TPL_CATS.map((c) => (
          <span
            key={c.name}
            className={`tpl-cat${tplCatFilter === c.name ? ' active' : ''}`}
            onClick={() => setTplCatFilter(c.name)}
          >
            {c.icon} {c.name}
          </span>
        ))}
      </div>

      {/* Grid */}
      <div className="tpl-grid">
        {tpls.map((t) => (
          <div className="tpl-card" key={t.id}>
            <div className="tpl-top">
              <span className="tpl-icon">{t.icon}</span>
              <span className="tpl-name">{t.name}</span>
            </div>
            <div className="tpl-desc">{t.desc}</div>
            <div className="tpl-footer">
              {t.depts.map((d) => (
                <span className="tpl-dept" key={d}>{d}</span>
              ))}
              <span className="tpl-est">
                {t.est} · {t.cost}
              </span>
              <button className="tpl-go" onClick={() => openForm(t)}>
                下旨
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Court Discuss Window */}
      {discussWindowOpen && (
        <div className="modal-bg open" onClick={() => setDiscussWindowOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setDiscussWindowOpen(false)}>✕</button>
            <div className="modal-body">
              <div style={{ fontSize: 11, color: 'var(--acc)', fontWeight: 700, letterSpacing: '.04em', marginBottom: 4 }}>
                御前议政窗口
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 6 }}>🪟 轮番议政 · 皇上拍板</div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
                当前会话：{discussSessionId || '未开始'} {discussResult?.status ? `| 状态：${discussResult.status}` : ''}
              </div>
              {discussLoading && (
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
                  ⏳ 议政处理中，请稍候…
                </div>
              )}
              {discussResult?.roundRunning && discussResult?.speakingNow?.agentLabel && (
                <div style={{ fontSize: 12, color: 'var(--acc)', marginBottom: 10 }}>
                  🔄 正在发言：第{discussResult.speakingNow.round || '?'}轮 · 第
                  {discussResult.speakingNow.turn || '?'}位/{discussResult.speakingNow.totalTurns || '?'} ·
                  {discussResult.speakingNow.agentLabel}
                </div>
              )}

              {discussResult?.topic && (
                <div
                  style={{
                    background: 'var(--panel2)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    padding: 10,
                    whiteSpace: 'pre-wrap',
                    lineHeight: 1.55,
                    marginBottom: 10,
                  }}
                >
                  议题：{discussResult.topic}
                </div>
              )}

              {discussResult?.assessment && !discussResult?.final && (
                <div
                  style={{
                    background: 'var(--panel2)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    padding: 10,
                    marginBottom: 10,
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 700, marginBottom: 4 }}>
                    {discussResult.assessment.moderatorLabel} 本轮建议：
                    {discussResult.assessment.recommend_stop ? '可结束讨论' : '建议继续讨论'}
                  </div>
                  {discussResult.assessment.reason && (
                    <div style={{ color: 'var(--muted)', marginBottom: 4 }}>原因：{discussResult.assessment.reason}</div>
                  )}
                  {discussResult.assessment.question_to_emperor && (
                    <div>请皇上拍板：{discussResult.assessment.question_to_emperor}</div>
                  )}
                </div>
              )}

              {discussResult?.final && (
                <div
                  style={{
                    background: 'var(--panel2)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    padding: 10,
                    marginBottom: 10,
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 700, marginBottom: 6 }}>
                    {discussResult.final.ready_for_edict ? '✅ 已形成办理结论' : '🟡 结论暂不建议办理'}
                  </div>
                  {discussResult.final.clarified_goal && (
                    <div style={{ color: 'var(--muted)', marginBottom: 6 }}>
                      目标澄清：{discussResult.final.clarified_goal}
                    </div>
                  )}
                  <div
                    style={{
                      background: 'var(--panel)',
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      padding: 8,
                      whiteSpace: 'pre-wrap',
                      lineHeight: 1.5,
                    }}
                  >
                    {discussResult.final.recommended_edict || '（暂无草案）'}
                  </div>
                </div>
              )}

              {discussResult?.linkedTaskId && (
                <div style={{ fontSize: 12, color: 'var(--acc)', marginBottom: 10 }}>
                  已交由太子办理：{discussResult.linkedTaskId}
                </div>
              )}

              <div
                style={{
                  border: '1px solid var(--line)',
                  borderRadius: 8,
                  maxHeight: 360,
                  overflow: 'auto',
                  padding: 10,
                  background: 'var(--panel2)',
                }}
              >
                {(discussResult?.discussion || []).length === 0 && (
                  <div style={{ color: 'var(--muted)', fontSize: 12 }}>暂无讨论记录</div>
                )}
                {(discussResult?.discussion || []).map((x, i) => (
                  <div
                    key={`${x.round}-${x.agentId}-${i}`}
                    style={{
                      border: x.status === 'speaking' ? '1px solid #4da3ff66' : x.error ? '1px solid #ff7d7d66' : '1px solid var(--line)',
                      borderRadius: 8,
                      padding: 8,
                      marginBottom: 8,
                      background: x.status === 'speaking' ? '#4da3ff12' : x.error ? '#ff7d7d12' : 'var(--panel)',
                    }}
                  >
                    <div style={{ fontWeight: 700, marginBottom: 4 }}>
                      第{x.round}轮 · 第{x.turn || '?'}位/{x.totalTurns || '?'} · {x.agentLabel}
                      {x.status === 'speaking' && <span style={{ marginLeft: 8, color: '#4da3ff' }}>（发言中）</span>}
                      {x.error && <span style={{ marginLeft: 8, color: '#ff7d7d' }}>（发言降级）</span>}
                    </div>
                    <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5, fontSize: 12 }}>
                      {x.reply || (x.status === 'speaking' ? '……' : '（暂无内容）')}
                    </div>
                  </div>
                ))}
              </div>

              <div style={{ marginTop: 12, marginBottom: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>皇上发言（本轮批示）</div>
                <textarea
                  className="tpl-input"
                  style={{ minHeight: 56, resize: 'vertical' }}
                  placeholder="例如：请收敛到可执行方案；若仍有模型兼容问题，直接给出是否终止建议。"
                  value={emperorNote}
                  onChange={(e) => setEmperorNote(e.target.value)}
                  disabled={discussLoading}
                />
                {(discussResult?.emperorNotes || []).length > 0 && (
                  <div style={{ marginTop: 6, fontSize: 12, color: 'var(--muted)' }}>
                    最近批示：{(discussResult?.emperorNotes || []).slice(-1)[0]?.text}
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                <button type="button" className="btn btn-g" onClick={refreshCourtDiscussStatus} disabled={discussLoading}>
                  刷新
                </button>
                {discussResult?.status === 'ongoing' && (
                  <>
                    <button
                      type="button"
                      className="btn btn-g"
                      onClick={continueCourtDiscuss}
                      disabled={discussLoading || Boolean(discussResult?.roundRunning)}
                    >
                      继续一轮
                    </button>
                    <button
                      type="button"
                      className="tpl-go"
                      onClick={finalizeCourtDiscuss}
                      disabled={discussLoading || Boolean(discussResult?.roundRunning)}
                    >
                      拍板并生成旨意
                    </button>
                    <button
                      type="button"
                      className="btn btn-g"
                      style={{ borderColor: '#ff7d7d', color: '#ff7d7d' }}
                      onClick={terminateCourtDiscuss}
                      disabled={discussLoading}
                    >
                      终止话题
                    </button>
                  </>
                )}
                {discussResult?.status === 'done' && !discussResult?.linkedTaskId && (
                  <>
                    <button type="button" className="btn btn-g" onClick={adoptDiscussEdict}>
                      采用到自由下旨
                    </button>
                    <button
                      type="button"
                      className="btn btn-g"
                      style={{ borderColor: '#ff7d7d', color: '#ff7d7d' }}
                      onClick={terminateCourtDiscuss}
                      disabled={discussLoading}
                    >
                      终止话题
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Template Form Modal */}
      {formTpl && (
        <div className="modal-bg open" onClick={() => setFormTpl(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setFormTpl(null)}>✕</button>
            <div className="modal-body">
              <div style={{ fontSize: 11, color: 'var(--acc)', fontWeight: 700, letterSpacing: '.04em', marginBottom: 4 }}>
                圣旨模板
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 6 }}>
                {formTpl.icon} {formTpl.name}
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 18 }}>{formTpl.desc}</div>
              <div style={{ display: 'flex', gap: 6, marginBottom: 18, flexWrap: 'wrap' }}>
                {formTpl.depts.map((d) => (
                  <span className="tpl-dept" key={d}>{d}</span>
                ))}
                <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
                  {formTpl.est} · {formTpl.cost}
                </span>
              </div>

              <form className="tpl-form" onSubmit={execute}>
                {formTpl.params.map((p) => (
                  <div className="tpl-field" key={p.key}>
                    <label className="tpl-label">
                      {p.label}
                      {p.required && <span style={{ color: '#ff5270' }}> *</span>}
                    </label>
                    {p.type === 'textarea' ? (
                      <textarea
                        className="tpl-input"
                        style={{ minHeight: 80, resize: 'vertical' }}
                        required={p.required}
                        value={formVals[p.key] || ''}
                        onChange={(e) => setFormVals((v) => ({ ...v, [p.key]: e.target.value }))}
                      />
                    ) : p.type === 'select' ? (
                      <select
                        className="tpl-input"
                        value={formVals[p.key] || p.default || ''}
                        onChange={(e) => setFormVals((v) => ({ ...v, [p.key]: e.target.value }))}
                      >
                        {(p.options || []).map((o) => (
                          <option key={o}>{o}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        className="tpl-input"
                        type="text"
                        required={p.required}
                        value={formVals[p.key] || ''}
                        onChange={(e) => setFormVals((v) => ({ ...v, [p.key]: e.target.value }))}
                      />
                    )}
                  </div>
                ))}

                {previewCmd && (
                  <div
                    style={{
                      background: 'var(--panel2)',
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      padding: 12,
                      marginBottom: 14,
                      fontSize: 12,
                      color: 'var(--muted)',
                    }}
                  >
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>
                      📜 将发送给中书省的旨意：
                    </div>
                    <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{previewCmd}</div>
                  </div>
                )}

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  <button type="button" className="btn btn-g" onClick={preview} style={{ padding: '8px 16px', fontSize: 12 }}>
                    👁 预览旨意
                  </button>
                  <button type="submit" className="tpl-go" style={{ padding: '8px 20px', fontSize: 13 }}>
                    📜 下旨
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
