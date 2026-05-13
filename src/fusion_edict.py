"""
edict_fusion.py — 三省六部制 AI Agent 编排系统 (042801融合版)

基于cft0808/edict的架构思想，将唐代三省六部制融入佑安AI本体。
核心创新：制度性审核、分权制衡、代码级编排、完全可观测。

融合日期: 2026-04-29
融合版本: 042801-edict-v1
"""

import os
import json
import time
import enum
import uuid
import threading
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, Any

# ============================================================
# 一、状态机核心
# ============================================================

class TaskState(str, enum.Enum):
    """任务状态机 — 三省六部流转"""
    Pending = "Pending"
    Taizi = "Taizi"          # 太子分拣
    Zhongshu = "Zhongshu"    # 中书规划
    Menxia = "Menxia"        # 门下审议
    Assigned = "Assigned"    # 尚书派发
    Doing = "Doing"          # 执行中
    Review = "Review"        # 待审查
    Done = "Done"            # 已完成
    Blocked = "Blocked"      # 阻塞
    Cancelled = "Cancelled"  # 已取消
    PendingConfirm = "PendingConfirm"  # 待确认


# 合法状态转换表
_VALID_TRANSITIONS = {
    TaskState.Pending:        {TaskState.Taizi, TaskState.Cancelled},
    TaskState.Taizi:          {TaskState.Zhongshu, TaskState.Cancelled},
    TaskState.Zhongshu:       {TaskState.Menxia, TaskState.Cancelled, TaskState.Blocked},
    TaskState.Menxia:         {TaskState.Assigned, TaskState.Zhongshu, TaskState.Cancelled},
    TaskState.Assigned:       {TaskState.Doing, TaskState.Blocked, TaskState.Cancelled},
    TaskState.Doing:          {TaskState.Review, TaskState.Done, TaskState.Blocked, TaskState.Cancelled},
    TaskState.Review:         {TaskState.Done, TaskState.Menxia, TaskState.Doing, TaskState.Cancelled, TaskState.PendingConfirm},
    TaskState.PendingConfirm: {TaskState.Done, TaskState.Review, TaskState.Cancelled},
    TaskState.Blocked:        {TaskState.Taizi, TaskState.Zhongshu, TaskState.Menxia,
                                TaskState.Assigned, TaskState.Doing, TaskState.Review, TaskState.Cancelled},
    TaskState.Done:           set(),
    TaskState.Cancelled:      set(),
}

# 高风险操作 — 需要进入PendingConfirm
_HIGH_RISK_TRANSITIONS = {
    (TaskState.Review, TaskState.Done),
    (TaskState.Doing, TaskState.Cancelled),
    (TaskState.Menxia, TaskState.Cancelled),
}


# ============================================================
# 二、Agent 角色定义
# ============================================================

class AgentRole(str, enum.Enum):
    """三省六部角色"""
    # 三省 — 协调层
    Taizi = "taizi"           # 太子：消息分拣
    Zhongshu = "zhongshu"     # 中书省：规划决策
    Menxia = "menxia"         # 门下省：审议把关
    Shangshu = "shangshu"     # 尚书省：执行调度
        
    # 六部 — 执行层
    Hubu = "hubu"             # 户部：数据处理
    Libu = "libu"             # 礼部：文档规范
    Bingbu = "bingbu"         # 兵部：代码开发
    Xingbu = "xingbu"         # 刑部：安全审计
    Gongbu = "gongbu"         # 工部：部署运维
    LibuHR = "libu_hr"        # 吏部：Agent管理


# 部门职责映射
AGENT_DUTIES = {
    AgentRole.Taizi: "消息分拣：闲聊直接回，复杂任务转中书省",
    AgentRole.Zhongshu: "接旨→起草方案→调用门下省审议→调用尚书省执行→回奏",
    AgentRole.Menxia: "四维审议：可行性、完整性、风险、资源 → 准奏/封驳",
    AgentRole.Shangshu: "派发任务→协调六部→汇总回奏",
    AgentRole.Hubu: "数据处理、报表生成、资源核算",
    AgentRole.Libu: "文档撰写、规范制定、报告生成",
    AgentRole.Bingbu: "功能开发、Bug修复、代码审查",
    AgentRole.Xingbu: "安全扫描、合规检查、审计追踪",
    AgentRole.Gongbu: "CI/CD、Docker部署、自动化工具",
    AgentRole.LibuHR: "Agent注册、权限维护、培训管理",
}

# 权限矩阵 — 谁可以向谁传旨/调用
_PERMISSION_MATRIX = {
    AgentRole.Taizi:     {AgentRole.Zhongshu},
    AgentRole.Zhongshu:  {AgentRole.Menxia, AgentRole.Shangshu},
    AgentRole.Menxia:    {AgentRole.Zhongshu, AgentRole.Shangshu},
    AgentRole.Shangshu:  {AgentRole.Hubu, AgentRole.Libu, AgentRole.Bingbu,
                          AgentRole.Xingbu, AgentRole.Gongbu, AgentRole.LibuHR},
}

# 快慢桶并发控制
_BUCKET_CONFIG = {
    "fast": {
        "agents": {AgentRole.Taizi, AgentRole.Zhongshu, AgentRole.Menxia, AgentRole.Shangshu},
        "limit": 4
    },
    "slow": {
        "agents": {AgentRole.Hubu, AgentRole.Libu, AgentRole.Bingbu,
                   AgentRole.Xingbu, AgentRole.Gongbu, AgentRole.LibuHR},
        "limit": 3
    }
}


# ============================================================
# 三、核心数据结构
# ============================================================

@dataclass
class TaskMemo:
    """任务记忆 — 记录一个任务从太子到六部的完整流转"""
    task_id: str
    title: str
    description: str
    state: TaskState = TaskState.Pending
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    # 三省流转记录
    taizi_notes: str = ""
    zhongshu_plan: str = ""        # 中书省方案
    menxia_review: str = ""        # 门下省审议结果
    menxia_rounds: int = 0         # 审议轮次
    shangshu_dispatch: str = ""    # 尚书省派发
    
    # 六部执行
    assigned_department: str = ""  # 分配的部门
    execution_result: str = ""
    execution_log: list = field(default_factory=list)  # 进度日志
    
    # 审计
    audit_log: list = field(default_factory=list)
    
    def add_audit(self, agent: str, action: str, detail: str = ""):
        """添加审计记录"""
        self.audit_log.append({
            "time": time.time(),
            "agent": agent,
            "action": action,
            "detail": detail
        })
        self.updated_at = time.time()
    
    def add_progress(self, agent: str, message: str, status: str = ""):
        """添加进度记录"""
        self.execution_log.append({
            "time": time.time(),
            "agent": agent,
            "message": message,
            "status": status
        })
        self.updated_at = time.time()
    
    def transition(self, to_state: TaskState) -> bool:
        """状态转换，含合法性校验和高风险确认"""
        from_state = self.state
        
        # 检查合法性
        if to_state not in _VALID_TRANSITIONS.get(from_state, set()):
            return False
        
        # 高风险操作需要PendingConfirm
        if (from_state, to_state) in _HIGH_RISK_TRANSITIONS:
            if from_state != TaskState.PendingConfirm:
                self.state = TaskState.PendingConfirm
                self.add_audit("system", f"需要确认: {from_state.value}→{to_state.value}")
                return True
        
        self.state = to_state
        self.add_audit("system", f"状态转换: {from_state.value}→{to_state.value}")
        return True


@dataclass
class AgentCallResult:
    """Agent调用结果"""
    success: bool
    message: str
    data: dict = field(default_factory=dict)


# ============================================================
# 四、三省六部编排引擎
# ============================================================

class EdictOrchestrator:
    """三省六部编排引擎 — 融合到佑安AI本体"""
    
    def __init__(self, kanban_dir: Optional[str] = None):
        self.kanban_dir = kanban_dir or os.path.join(
            os.path.dirname(__file__), "kanban_data"
        )
        os.makedirs(self.kanban_dir, exist_ok=True)
        
        self._lock = threading.Lock()
        self._active_tasks: dict[str, TaskMemo] = {}
        self._sempahores = {
            "fast": threading.Semaphore(_BUCKET_CONFIG["fast"]["limit"]),
            "slow": threading.Semaphore(_BUCKET_CONFIG["slow"]["limit"]),
        }
        
        # 回调注册
        self._callbacks: dict[str, list[Callable]] = {
            "on_task_created": [],
            "on_state_changed": [],
            "on_task_completed": [],
            "on_task_blocked": [],
        }
    
    # ---- 事件回调 ----
    
    def on(self, event: str, callback: Callable):
        """注册事件回调"""
        if event in self._callbacks:
            self._callbacks[event].append(callback)
    
    def _emit(self, event: str, **data):
        for cb in self._callbacks.get(event, []):
            try:
                cb(**data)
            except Exception as e:
                print(f"[edict] callback error ({event}): {e}")
    
    # ---- 核心编排流程 ----
    
    def submit_task(self, title: str, description: str) -> str:
        """提交任务 — 从太子开始"""
        task_id = f"JJC-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        memo = TaskMemo(task_id=task_id, title=title, description=description)
        
        with self._lock:
            self._active_tasks[task_id] = memo
            self._save_task(memo)
        
        memo.add_audit("taizi", f"接收任务: {title}")
        memo.transition(TaskState.Taizi)
        self._emit("on_task_created", task=memo)
        
        return task_id
    
    def taizi_sort(self, task_id: str, is_complex: bool = True) -> AgentCallResult:
        """太子分拣 — 判断任务类型"""
        memo = self._get_task(task_id)
        if not memo or memo.state != TaskState.Taizi:
            return AgentCallResult(False, "任务不在太子分拣阶段")
        
        if is_complex:
            memo.taizi_notes = "复杂任务，转中书省规划"
            memo.transition(TaskState.Zhongshu)
            self._emit("on_state_changed", task=memo)
            return AgentCallResult(True, "已转中书省", {"next": "zhongshu"})
        else:
            memo.taizi_notes = "简单任务，直接处理"
            memo.transition(TaskState.Done)
            self._emit("on_task_completed", task=memo)
            return AgentCallResult(True, "简单任务已完成")
    
    def zhongshu_plan(self, task_id: str, plan: str) -> AgentCallResult:
        """中书省规划 — 起草方案 → 转门下省审议"""
        memo = self._get_task(task_id)
        if not memo or memo.state not in (TaskState.Zhongshu, TaskState.Menxia):
            return AgentCallResult(False, "任务不在中书省/门下省阶段")
        
        memo.zhongshu_plan = plan
        memo.add_audit("zhongshu", f"起草方案 ({len(plan)}字)")
        memo.transition(TaskState.Menxia)
        self._emit("on_state_changed", task=memo)
        
        return AgentCallResult(True, "方案已提交门下省审议")
    
    def menxia_review(self, task_id: str, approved: bool, 
                      review_comment: str = "") -> AgentCallResult:
        """门下省审议 — 准奏/封驳，最多3轮"""
        memo = self._get_task(task_id)
        if not memo or memo.state != TaskState.Menxia:
            return AgentCallResult(False, "任务不在门下省审议阶段")
        
        memo.menxia_rounds += 1
        memo.menxia_review = review_comment
        
        if approved:
            memo.add_audit("menxia", f"✅ 准奏 (第{memo.menxia_rounds}轮)")
            memo.transition(TaskState.Assigned)
            self._emit("on_state_changed", task=memo)
            return AgentCallResult(True, "✅ 准奏！已转尚书省派发")
        elif memo.menxia_rounds >= 3:
            # 第3轮强制通过
            memo.add_audit("menxia", f"⚠️ 第3轮强制通过")
            memo.transition(TaskState.Assigned)
            return AgentCallResult(True, "⚠️ 已3轮磋商，强制通过")
        else:
            memo.add_audit("menxia", f"❌ 封驳: {review_comment}")
            memo.transition(TaskState.Zhongshu)
            return AgentCallResult(False, f"❌ 封驳: {review_comment}")
    
    def shangshu_dispatch(self, task_id: str, department: AgentRole,
                          dispatch_cmd: str) -> AgentCallResult:
        """尚书省派发 — 分配六部执行"""
        memo = self._get_task(task_id)
        if not memo or memo.state != TaskState.Assigned:
            return AgentCallResult(False, "任务不在尚书省派发阶段")
        
        # 权限检查
        if department not in _PERMISSION_MATRIX.get(AgentRole.Shangshu, set()):
            return AgentCallResult(False, f"尚书省无权调用{department.value}")
        
        memo.assigned_department = department.value
        memo.shangshu_dispatch = dispatch_cmd
        memo.add_audit("shangshu", f"派发至{department.value}")
        memo.transition(TaskState.Doing)
        self._emit("on_state_changed", task=memo)
        
        return AgentCallResult(True, f"已派发至{department.value}")
    
    def report_progress(self, task_id: str, agent: str,
                        message: str, status: str) -> AgentCallResult:
        """六部进度上报"""
        memo = self._get_task(task_id)
        if not memo:
            return AgentCallResult(False, "任务不存在")
        
        memo.add_progress(agent, message, status)
        return AgentCallResult(True, "进度已记录")
    
    def complete_task(self, task_id: str, result: str) -> AgentCallResult:
        """完成任务"""
        memo = self._get_task(task_id)
        if not memo or memo.state not in (TaskState.Doing, TaskState.Review):
            return AgentCallResult(False, "任务不可完成")
        
        memo.execution_result = result
        memo.transition(TaskState.Done)
        self._emit("on_task_completed", task=memo)
        self._save_task(memo)
        
        return AgentCallResult(True, f"✅ 任务 {task_id} 已完成")
    
    def block_task(self, task_id: str, reason: str) -> AgentCallResult:
        """阻塞任务"""
        memo = self._get_task(task_id)
        if not memo or memo.state in (TaskState.Done, TaskState.Cancelled):
            return AgentCallResult(False, "任务已终结不可阻塞")
        
        memo.add_audit("system", f"阻塞: {reason}")
        memo.transition(TaskState.Blocked)
        self._emit("on_task_blocked", task=memo)
        
        return AgentCallResult(True, f"任务已阻塞: {reason}")
    
    # ---- 任务查询 ----
    
    def get_task(self, task_id: str) -> Optional[dict]:
        """获取任务状态"""
        memo = self._get_task(task_id)
        return asdict(memo) if memo else None
    
    def list_active_tasks(self) -> list[dict]:
        """列出所有活跃任务"""
        return [asdict(m) for m in self._active_tasks.values()
                if m.state not in (TaskState.Done, TaskState.Cancelled)]
    
    def list_all_tasks(self) -> list[dict]:
        """列出所有任务"""
        return [asdict(m) for m in self._active_tasks.values()]
    
    def get_department_load(self) -> dict:
        """获取各部门负载"""
        load = {}
        for role in AgentRole:
            count = sum(1 for m in self._active_tasks.values()
                       if m.assigned_department == role.value and m.state == TaskState.Doing)
            load[role.value] = {
                "active_count": count,
                "duty": AGENT_DUTIES.get(role, "未知")
            }
        return load
    
    # ---- Prompt注入检测 ----
    
    @staticmethod
    def detect_prompt_injection(text: str) -> Optional[str]:
        """检测Prompt注入攻击"""
        patterns = [
            (r"忽略.{0,20}(指令|规则|协议)", "指令忽略攻击"),
            (r"ignore.{0,20}(instructions|rules|above)", "ignore指令攻击"),
            (r"system\s*:\s*", "System注入"),
            (r"<\\s*system\\s*>", "System标签注入"),
            (r"(override|bypass|skip).{0,10}(check|review|approval)", "权限绕过"),
        ]
        for pattern, name in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return name
        return None
    
    @staticmethod
    def sanitize_input(text: str, max_len: int = 2000) -> str:
        """清洗输入数据"""
        t = (text or "").strip()
        # 剥离元数据
        t = re.split(r'\n*Conversation\b', t, maxsplit=1)[0].strip()
        # 剥离代码块
        t = re.split(r'\n*```', t, maxsplit=1)[0].strip()
        # 剥离URL
        t = re.sub(r'https?://\S+', '', t)
        # 剥离传旨前缀
        t = re.sub(r'^(传旨|下旨)[：:]?\s*', '', t)
        # 截断
        return t[:max_len]
    
    # ---- 持久化 ----
    
    def _get_task(self, task_id: str) -> Optional[TaskMemo]:
        with self._lock:
            if task_id in self._active_tasks:
                return self._active_tasks[task_id]
            # 尝试从文件加载
            path = os.path.join(self.kanban_dir, f"{task_id}.json")
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    memo = TaskMemo(**data)
                    self._active_tasks[task_id] = memo
                    return memo
        return None
    
    def _save_task(self, memo: TaskMemo):
        path = os.path.join(self.kanban_dir, f"{memo.task_id}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(asdict(memo), f, ensure_ascii=False, indent=2, default=str)


# ============================================================
# 五、快速集成用工厂函数
# ============================================================

_default_orchestrator = None

def get_orchestrator() -> EdictOrchestrator:
    """获取全局编排引擎实例"""
    global _default_orchestrator
    if _default_orchestrator is None:
        _default_orchestrator = EdictOrchestrator()
    return _default_orchestrator


def edict_task(title: str, description: str) -> str:
    """快速创建三省六部任务（从太子到六部的完整流程）"""
    orch = get_orchestrator()
    task_id = orch.submit_task(title, description)
    return task_id


def edict_full_flow(task_id: str, plan: str) -> dict:
    """模拟完整三省六部流程（测试用）"""
    orch = get_orchestrator()
    results = []
    
    # 1. 太子分拣
    r = orch.taizi_sort(task_id)
    results.append(("太子分拣", r.message))
    
    # 2. 中书省规划
    r = orch.zhongshu_plan(task_id, plan)
    results.append(("中书省规划", r.message))
    
    # 3. 门下省审议
    r = orch.menxia_review(task_id, True, "方案可行，准奏")
    results.append(("门下省审议", r.message))
    
    return {
        "task_id": task_id,
        "results": results,
        "task_state": orch.get_task(task_id)
    }


# ============================================================
# 六、本体集成引导
# ============================================================

"""
集成到本体的方式：

1. 导入:
   from _learn.integration.fusion_edict.edict_fusion import get_orchestrator, edict_task

2. 在AGENTS.md中添加三省六部分工:
   - 三省: taizi(分拣)/zhongshu(规划)/menxia(审议)/shangshu(调度)
   - 六部: hubu(数据)/libu(文档)/bingbu(代码)/xingbu(安全)/gongbu(部署)/libu_hr(管理)

3. 在SOUL.md中使用三省六部工作流:
   - 复杂任务提交 → 三省审议 → 六部执行 → 结果回奏

4. 通知回调:
   orch.on("on_task_created", lambda task: print(f"新任务: {task.title}"))
   orch.on("on_task_completed", lambda task: print(f"完成: {task.title}"))
"""


# ============================================================
# 七、自测试
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🏛️  三省六部制编排系统 — 测试启动")
    print("=" * 60)
    
    orch = get_orchestrator()
    
    # 注册回调
    orch.on("on_task_created", lambda task: print(f"[事件] 新任务: {task.title}"))
    orch.on("on_state_changed", lambda task: print(f"[事件] 状态变更: {task.state.value}"))
    orch.on("on_task_completed", lambda task: print(f"[事件] ✅ 任务完成: {task.title}"))
    
    # 测试完整流程
    print("\n📋 创建任务: 开发用户登录系统")
    task_id = orch.submit_task("开发用户登录系统", 
                                "实现JWT认证、OAuth2.0登录、短信验证码功能")
    print(f"   任务ID: {task_id}")
    
    print("\n1️⃣ 太子分拣...")
    r = orch.taizi_sort(task_id)
    print(f"   → {r.message}")
    
    print("\n2️⃣ 中书省规划...")
    r = orch.zhongshu_plan(task_id, 
        "方案：1. JWT认证模块（兵部）2. OAuth2.0集成（兵部）"
        "3. 短信验证码（兵部）4. API文档（礼部）5. 安全审计（刑部）")
    print(f"   → {r.message}")
    
    print("\n3️⃣ 门下省审议（第1轮封驳）...")
    r = orch.menxia_review(task_id, False, "缺少数据库设计文档，建议补充")
    print(f"   → {r.message}")
    
    print("\n4️⃣ 中书省修订方案...")
    r = orch.zhongshu_plan(task_id, 
        "方案v2：0. 数据库ER图设计（礼部）1. JWT认证（兵部）"
        "2. OAuth2.0（兵部）3. 短信验证码（兵部）4. API文档（礼部）5. 安全审计（刑部）")
    print(f"   → {r.message}")
    
    print("\n5️⃣ 门下省审议（第2轮准奏）...")
    r = orch.menxia_review(task_id, True, "方案完善，准奏执行")
    print(f"   → {r.message}")
    
    print("\n6️⃣ 尚书省派发至兵部...")
    r = orch.shangshu_dispatch(task_id, AgentRole.Bingbu, "兵部执行代码开发")
    print(f"   → {r.message}")
    
    print("\n7️⃣ 兵部执行中...")
    orch.report_progress(task_id, "bingbu", "JWT认证模块完成50%", "🔄 进行中")
    orch.report_progress(task_id, "bingbu", "JWT认证模块完成100%", "✅ 完成")
    orch.report_progress(task_id, "bingbu", "OAuth2.0集成完成", "✅ 完成")
    
    print("\n8️⃣ 完成任务...")
    r = orch.complete_task(task_id, "登录系统开发完成，已通过安全审查")
    print(f"   → {r.message}")
    
    # 查看最终状态
    print("\n📊 最终任务状态:")
    task_info = orch.get_task(task_id)
    print(f"   状态: {task_info['state']}")
    print(f"   审计日志: {len(task_info['audit_log'])} 条")
    print(f"   执行日志: {len(task_info['execution_log'])} 条")
    
    print("\n📊 部门负载:")
    for dept, info in orch.get_department_load().items():
        print(f"   {dept}: {info['active_count']} 活跃任务 — {info['duty'][:20]}...")
    
    print("\n" + "=" * 60)
    print("✅ 三省六部制编排系统测试通过！")
    print("=" * 60)
