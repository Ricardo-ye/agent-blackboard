/**
 * 演示数据灌入脚本（前端版）
 *
 * 一键生成一批智能体 / 知识条目 / 任务，并把流程推进到「有冲突、有依赖、
 * 有分配、有已完成」的丰富状态，方便空库时立刻体验全部页面。
 * 每一步都做了容错：单个失败不影响后续，最后汇总成功/失败数。
 */
import { api } from './api.js';

const AGENTS = [
  { name: '架构师Agent', capabilities: ['architecture', 'design'], endpoint: null },
  { name: '编码Agent',   capabilities: ['python', 'code_gen'] },
  { name: '检索Agent',   capabilities: ['search', 'nlp'] },
  { name: '测试Agent',   capabilities: ['testing', 'qa'] },
  { name: '评审Agent',   capabilities: ['review', 'design'] },
];

export async function seedDemoData(onProgress = () => {}) {
  const result = { agents: 0, entries: 0, tasks: 0, conflicts: 0, errors: [] };
  const guard = async (label, fn) => {
    try { return await fn(); } catch (e) {
      result.errors.push(`${label}：${e.message}`);
      return null;
    }
  };

  // 1. 智能体 -------------------------------------------------------------
  const created = [];
  for (const spec of AGENTS) {
    onProgress(`注册智能体 ${spec.name}`);
    const a = await guard(`注册 ${spec.name}`, () => api.createAgent(spec));
    if (a) { created.push(a); result.agents += 1; }
  }
  if (!created.length) return result;

  const byName = (n) => created.find((a) => a.name === n)?.agent_id || created[0].agent_id;
  const arch = byName('架构师Agent');
  const coder = byName('编码Agent');
  const searcher = byName('检索Agent');
  const tester = byName('测试Agent');
  const reviewer = byName('评审Agent');

  // 2. 知识条目 -----------------------------------------------------------
  // architecture 主题下刻意写入两条观点不同的条目，用于触发「意见冲突」
  const ENTRIES = [
    { author: arch,     topic: 'architecture', content: { pattern: 'blackboard', note: '以黑板为中枢，智能体只与黑板交互' }, tags: ['架构', '模式'], priority: 'high' },
    { author: reviewer, topic: 'architecture', content: { pattern: 'message_bus', note: '应改为消息总线，黑板会成为瓶颈' }, tags: ['架构', '争议'], priority: 'high' },
    { author: coder,    topic: 'implementation', content: { stack: ['FastAPI', 'SQLite', 'WebSocket'], optimistic_lock: true }, tags: ['实现'], priority: 'normal' },
    { author: searcher, topic: 'research', content: { papers: 3, conclusion: '黑板模式适合弱耦合多智能体场景' }, tags: ['调研'], priority: 'normal', confidence: 0.8 },
    { author: tester,   topic: 'test-plan', content: { cases: ['并发写入一致性', '依赖解锁', '冲突幂等'], coverage: '78%' }, tags: ['测试'], priority: 'normal' },
    { author: arch,     topic: 'risk', content: { risks: ['单写者瓶颈', '心跳误判离线'] }, tags: ['风险'], priority: 'critical' },
  ];

  for (const e of ENTRIES) {
    onProgress(`发布条目 ${e.topic}`);
    const r = await guard(`发布 ${e.topic}`, () =>
      api.createEntry(e.author, {
        topic: e.topic,
        content: e.content,
        tags: e.tags,
        priority: e.priority,
        confidence: e.confidence ?? 1.0,
      }));
    if (r) result.entries += 1;
  }

  // 3. 任务（含依赖链） ---------------------------------------------------
  // 注意：priority=high/critical 的任务创建后会命中默认规则自动分配
  onProgress('创建任务链');
  const t1 = await guard('创建架构设计任务', () => api.createTask({
    title: '确定系统架构方案',
    description: '对比黑板模式与消息总线，输出最终选型结论',
    required_capabilities: ['architecture'],
    priority: 'high',
  }));
  if (t1) result.tasks += 1;

  const t2 = await guard('创建编码任务', () => api.createTask({
    title: '实现黑板核心读写',
    description: '条目 CRUD + 乐观锁版本控制',
    required_capabilities: ['python'],
    priority: 'normal',
    dependencies: t1 ? [t1.task_id] : [],
  }));
  if (t2) result.tasks += 1;

  const t3 = await guard('创建调研任务', () => api.createTask({
    title: '调研多智能体协作范式',
    required_capabilities: ['search'],
    priority: 'critical',
  }));
  if (t3) result.tasks += 1;

  const t4 = await guard('创建测试任务', () => api.createTask({
    title: '编写并发一致性测试',
    required_capabilities: ['testing'],
    priority: 'normal',
    dependencies: [t2, t3].filter(Boolean).map((t) => t.task_id),
  }));
  if (t4) result.tasks += 1;

  const t5 = await guard('创建评审任务', () => api.createTask({
    title: '架构方案评审',
    required_capabilities: ['review'],
    priority: 'normal',
    dependencies: t1 ? [t1.task_id] : [],
  }));
  if (t5) result.tasks += 1;

  const t6 = await guard('创建无人可接任务', () => api.createTask({
    title: '量子加速算子优化',
    description: '故意声明一个无人具备的能力，用于演示「无可用智能体」',
    required_capabilities: ['quantum'],
    priority: 'low',
  }));
  if (t6) result.tasks += 1;

  // 4. 推进流程：完成 t1 解锁下游，再自动分配 -------------------------------
  if (t1) {
    onProgress('推进任务状态');
    await guard('完成架构任务', () =>
      api.updateTask(t1.task_id, arch, { status: 'done', result: { chosen: 'blackboard' } }));
  }
  if (t2) {
    await guard('自动分配编码任务', () => api.autoAssignTask(t2.task_id));
  }
  if (t3) {
    await guard('推进调研任务', () =>
      api.updateTask(t3.task_id, searcher, { status: 'in_progress' }));
  }
  if (t5) {
    await guard('推进评审任务', () =>
      api.updateTask(t5.task_id, reviewer, { status: 'in_progress' }));
  }

  // 5. 触发一次意见冲突检测（architecture 主题必然命中） ---------------------
  onProgress('检测意见冲突');
  const c = await guard('检测意见冲突', () => api.detectOpinion('architecture'));
  if (c && c.conflict) result.conflicts += 1;

  onProgress('完成');
  return result;
}
