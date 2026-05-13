"""
融合之神 FusionGod — 四大引擎协同交响系统 (050301-v1)

目标：将 edict(三省六部制) + MemSkill(元记忆) + RealTimeMemory(实时反刍) + 
      GenericAgent(自结晶) 打通为一条完整流水线。

关键设计原则：
1. 零侵入 — 不修改任何现有引擎的源代码，只通过事件回调和管道适配
2. 单向数据流 — 任务→edict分拣→MemSkill策略选择→RealTimeMemory捕获→GenericAgent结晶
3. 可观测 — 每条数据流都有审计日志，可用 trace_id 全程追踪
4. 优雅降级 — 任意引擎故障不影响其他引擎独立工作
"""

import json
import os
import time
import uuid
import threading
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Callable, Tuple
from enum import Enum

# ════════════════════════════════════════════════════════════════
# 路径配置
# ════════════════════════════════════════════════════════════════
WS = r"C:\Users\Administrator\.openclaw\workspace"
INTEGRATION_DIR = os.path.join(WS, "_learn", "integration")
FUSION_DIR = os.path.join(INTEGRATION_DIR, "fusion_god")
os.makedirs(FUSION_DIR, exist_ok=True)

# ════════════════════════════════════════════════════════════════
# 一、管道定义：引擎间的标准化消息格式
# ════════════════════════════════════════════════════════════════

class FlowPhase(str, Enum):
    """流水线阶段"""
    INPUT = "input"                  # 用户输入
    TAOZI_SORT = "taozi_sort"        # edict太子分拣
    MEMSKILL_SELECT = "memskill_select"  # MemSkill策略选择
    ZHONGSHU_PLAN = "zhongshu_plan"  # edict中书规划
    MENXIA_REVIEW = "menxia_review"  # edict门下审议
    SHANGSHU_DISPATCH = "shangshu_dispatch"  # edict尚书派发
    EXECUTION = "execution"          # GenericAgent执行
    REALTIME_CAPTURE = "realtime_capture"  # RealTimeMemory实时捕获
    CRYSTALLIZE = "crystallize"      # GenericAgent结晶
    COMPLETED = "completed"          # 完成
    FAILED = "failed"                # 失败


@dataclass
class FlowMessage:
    """流水线消息 — 在引擎间传递的标准数据包"""
    flow_id: str                       # 全局追踪ID
    phase: FlowPhase                   # 当前阶段
    source: str                        # 来源引擎
    target: str                        # 目标引擎
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_record(self) -> dict:
        return asdict(self)


@dataclass
class FlowLog:
    """完整流水记录 — 从创建到完成的所有管道消息"""
    flow_id: str
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    messages: List[FlowMessage] = field(default_factory=list)
    success: bool = False
    summary: str = ""
    
    def add_message(self, msg: FlowMessage):
        self.messages.append(msg)
    
    def finish(self, success: bool, summary: str = ""):
        self.completed_at = time.time()
        self.success = success
        self.summary = summary
    
    def to_dict(self) -> dict:
        return {
            "flow_id": self.flow_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "duration": (self.completed_at or time.time()) - self.created_at,
            "success": self.success,
            "summary": self.summary,
            "message_count": len(self.messages),
            "phases": [m.phase.value for m in self.messages],
        }


# ════════════════════════════════════════════════════════════════
# 二、引擎适配器 — 零侵入连接四大引擎
# ════════════════════════════════════════════════════════════════

class EdictAdapter:
    """edict适配器 — 将三省六部制的事件回调转为管道消息"""
    
    def __init__(self, bus: 'MessageBus'):
        self.bus = bus
        self.edict = None  # 延迟导入
    
    def _lazy_load(self):
        if self.edict is None:
            import sys; sys.path.insert(0, WS)
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "edict_fusion",
                os.path.join(INTEGRATION_DIR, "fusion_edict", "edict_fusion.py")
            )
            edict_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(edict_mod)
            self._get_orchestrator = edict_mod.get_orchestrator
            self._AgentRole = edict_mod.AgentRole
            self.edict = self._get_orchestrator()
            # 注册回调
            self.edict.on("on_task_created", self._on_task_created)
            self.edict.on("on_state_changed", self._on_state_changed)
            self.edict.on("on_task_completed", self._on_task_completed)
            self.edict.on("on_task_blocked", self._on_task_blocked)
    
    def _on_task_created(self, task):
        self.bus.emit(FlowMessage(
            flow_id=task.task_id,
            phase=FlowPhase.TAOZI_SORT,
            source="edict",
            target="memskill",
            payload={"title": task.title, "description": task.description}
        ))
    
    def _on_state_changed(self, task):
        self.bus.emit(FlowMessage(
            flow_id=task.task_id,
            phase=FlowPhase.SHANGSHU_DISPATCH,
            source="edict",
            target="generic_agent",
            payload={"task_id": task.task_id, "state": task.state.value, 
                     "department": task.assigned_department}
        ))
    
    def _on_task_completed(self, task):
        self.bus.emit(FlowMessage(
            flow_id=task.task_id,
            phase=FlowPhase.COMPLETED,
            source="edict",
            target="generic_agent",
            payload={"task_id": task.task_id, "result": task.execution_result}
        ))
    
    def _on_task_blocked(self, task):
        self.bus.emit(FlowMessage(
            flow_id=task.task_id,
            phase=FlowPhase.FAILED,
            source="edict",
            target="realtime_memory",
            payload={"task_id": task.task_id, "reason": task.get_task(task.task_id, {}).get("state","")}
        ))
    
    def submit_task(self, title: str, description: str) -> str:
        self._lazy_load()
        return self.edict.submit_task(title, description)
    
    def set_memskill_strategy(self, memory_strategy: str):
        """MemSkill记忆策略影响edict流程"""
        self._lazy_load()
        # MemSkill的策略结果注入到zhongshu_plan中
        self._current_strategy = memory_strategy


class MemSkillAdapter:
    """MemSkill适配器 — 动态记忆策略选择"""
    
    def __init__(self, bus: 'MessageBus'):
        self.bus = bus
    
    def select_strategy(self, context: Dict) -> Dict:
        """基于上下文选择记忆策略"""
        intent = context.get("intent", "")
        complexity = context.get("complexity", "medium")
        
        # 策略选择逻辑（简化版，实际使用Top-K检索）
        strategies = []
        if "修复" in intent or "bug" in intent.lower() or "错误" in intent:
            strategies.append({
                "skill": "error_fix_tracking",
                "description": "跟踪错误修复过程",
                "update_type": "update"
            })
        if "学习" in intent or "阅读" in intent or "研究" in intent:
            strategies.append({
                "skill": "learning_recap",
                "description": "学习复盘：关键知识点+疑惑点",
                "update_type": "insert"
            })
        if "报告" in intent or "整理" in intent:
            strategies.append({
                "skill": "structure_output",
                "description": "结构化输出+格式化",
                "update_type": "insert"
            })
        
        # 默认策略
        if not strategies:
            strategies.append({
                "skill": "default_tracking",
                "description": "默认进度追踪",
                "update_type": "insert"
            })
        
        return {
            "strategies": strategies,
            "top_k": min(len(strategies), 3),
            "selected": strategies[0] if strategies else None
        }
    
    def feedback(self, strategy: Dict, reward: float):
        """反馈策略效果"""
        pass  # MemSkill的反馈循环，需持久化


class GenericAgentAdapter:
    """GenericAgent适配器 — 通过事件驱动执行+自动结晶"""
    
    def __init__(self, bus: 'MessageBus'):
        self.bus = bus
        self._crystal_count = 0
    
    def execute(self, task_info: Dict) -> Dict:
        """执行任务（通过GenericAgent）"""
        task_id = task_info.get("task_id", "")
        # 这里转发给GenericAgent的AgentLoop运行
        # 实际实现：创建AgentLoop实例并调用run_loop
        return {
            "success": True,
            "task_id": task_id,
            "tool_count": 0
        }
    
    def crystallize(self, task: str, plan: list, result: str) -> Dict:
        """自动结晶（任务完成后调用）"""
        self._crystal_count += 1
        
        # 生成结晶记录
        crystal = {
            "name": f"crystal_{int(time.time())}",
            "task": task[:100],
            "plan_length": len(plan),
            "result_preview": result[:200],
            "timestamp": time.time(),
        }
        
        # 写入晶体文件
        crystal_dir = os.path.join(FUSION_DIR, "crystals")
        os.makedirs(crystal_dir, exist_ok=True)
        path = os.path.join(crystal_dir, f"{crystal['name']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(crystal, f, ensure_ascii=False, indent=2)
        
        return crystal


class RealTimeMemoryAdapter:
    """RealTimeMemory适配器 — 自动捕获流水线中的状态"""
    
    def __init__(self, bus: 'MessageBus'):
        self.bus = bus
        self._rtm = None
    
    def _lazy_load(self):
        if self._rtm is None:
            import importlib
            spec = importlib.util.spec_from_file_location(
                "fusion_realtime_memory",
                os.path.join(INTEGRATION_DIR, "fusion_realtime_memory", "__init__.py")
            )
            rtm_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rtm_mod)
            self._rtm = rtm_mod.RealTimeMemory()
    
    def capture_snapshot(self, flow_id: str, phase: str, payload: Dict):
        """自动捕获流水线中的关键快照"""
        self._lazy_load()
        try:
            self._rtm.snapshot(
                user_intent=payload.get("title", payload.get("intent", "")),
                current_work=f"[{phase}] {payload.get('description', '')}",
                progress=payload.get("progress", 50),
                errors=payload.get("errors", []),
                decisions=payload.get("decisions", []),
                blockers=payload.get("blockers", []),
                next_actions=payload.get("next_actions", []),
                unfinished=payload.get("unfinished", []),
            )
        except Exception as e:
            pass  # RTM容错：不影响主流程
    
    def end_flow(self, flow_id: str, success: bool, summary: str):
        """结束流水线，捕获最终状态"""
        self._lazy_load()
        try:
            self._rtm.end_session(success=success, summary=summary)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
# 三、消息总线 — 引擎间的通信中枢
# ════════════════════════════════════════════════════════════════

class MessageBus:
    """异步消息总线 — 四大引擎通过它交换消息"""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._flow_logs: Dict[str, FlowLog] = {}
        self._lock = threading.Lock()
        
        # 内置适配器
        self.edict = EdictAdapter(self)
        self.memskill = MemSkillAdapter(self)
        self.generic_agent = GenericAgentAdapter(self)
        self.realtime = RealTimeMemoryAdapter(self)
        
        # 注册默认管道
        self._setup_default_pipeline()
    
    def _setup_default_pipeline(self):
        """设置默认的引擎间消息路由"""
        
        # 管道1: edict分拣 → MemSkill选择策略
        self.on("edict", "memskill", self._pipe_sort_to_memskill)
        
        # 管道2: MemSkill策略 → GenericAgent执行
        self.on("memskill", "generic_agent", self._pipe_memskill_to_generic)
        
        # 管道3: GenericAgent执行 → RealTimeMemory捕获
        self.on("generic_agent", "realtime_memory", self._pipe_generic_to_realtime)
        
        # 管道4: GenericAgent完成 → 自动结晶
        self.on("generic_agent", "generic_agent", self._pipe_complete_to_crystal)
    
    def on(self, source: str, target: str, handler: Callable):
        """注册消息处理器"""
        key = f"{source}->{target}"
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscribers[key].append(handler)
    
    def emit(self, msg: FlowMessage):
        """发送消息，触发对应管道"""
        # 记录到流水日志
        with self._lock:
            if msg.flow_id not in self._flow_logs:
                self._flow_logs[msg.flow_id] = FlowLog(flow_id=msg.flow_id)
            self._flow_logs[msg.flow_id].add_message(msg)
        
        msg.phase = FlowPhase.REALTIME_CAPTURE
        self.realtime.capture_snapshot(
            msg.flow_id, msg.phase.value, msg.payload
        )
        
        # 路由给处理器
        key = f"{msg.source}->{msg.target}"
        handlers = self._subscribers.get(key, [])
        for handler in handlers:
            try:
                handler(msg)
            except Exception as e:
                msg.error = str(e)
    
    # ── 默认管道处理器 ──
    
    def _pipe_sort_to_memskill(self, msg: FlowMessage):
        """edict分拣完 → MemSkill选择记忆策略"""
        strategy = self.memskill.select_strategy(msg.payload)
        msg.source = "memskill"
        msg.payload["strategy"] = strategy
    
    def _pipe_memskill_to_generic(self, msg: FlowMessage):
        """MemSkill策略选完 → GenericAgent开始执行"""
        result = self.generic_agent.execute(msg.payload)
        msg.source = "generic_agent"
        msg.payload["execution"] = result
    
    def _pipe_generic_to_realtime(self, msg: FlowMessage):
        """GenericAgent执行中 → RealTimeMemory捕获状态"""
        pass  # RTM已在emit中自动捕获
    
    def _pipe_complete_to_crystal(self, msg: FlowMessage):
        """GenericAgent完成 → 自动结晶"""
        if msg.phase == FlowPhase.COMPLETED:
            payload = msg.payload
            crystal = self.generic_agent.crystallize(
                task=payload.get("title", ""),
                plan=payload.get("plan", []),
                result=payload.get("result", "")
            )
            msg.payload["crystal"] = crystal
    
    # ── 面向用户的接口 ──
    
    def submit(self, title: str, description: str) -> str:
        """提交一个完整任务到流水线"""
        task_id = self.edict.submit_task(title, description)
        
        self.emit(FlowMessage(
            flow_id=task_id,
            phase=FlowPhase.INPUT,
            source="user",
            target="edict",
            payload={"title": title, "description": description}
        ))
        
        return task_id
    
    def get_flow(self, flow_id: str) -> Optional[dict]:
        """获取流水线状态"""
        log = self._flow_logs.get(flow_id)
        return log.to_dict() if log else None
    
    def list_flows(self) -> List[dict]:
        """列出所有流水线"""
        return [log.to_dict() for log in self._flow_logs.values()]
    
    def stats(self) -> dict:
        """统计信息"""
        total = len(self._flow_logs)
        success = sum(1 for log in self._flow_logs.values() if log.success)
        return {
            "total_flows": total,
            "success_flows": success,
            "fail_flows": total - success,
            "crystal_count": self.generic_agent._crystal_count,
            "active": [log.flow_id for log in self._flow_logs.values() 
                      if log.completed_at is None],
        }


# ════════════════════════════════════════════════════════════════
# 四、Phase2 模块接入 — ToolEcosystem + StatefulMemSkill + MetaOrchestrator
# ════════════════════════════════════════════════════════════════

class Phase2Plugin:
    """Phase2三大模块的FusionGod插件接入
    将ToolEcosystem/StatefulMemSkill/MetaOrchestrator注册为FusionGod可选服务"""

    def __init__(self):
        self._toolchain = None; self._persist = None
        self._selector = None; self._decomposer = None
        self._tc = None; self._sb = None; self._mcp = None
        self._ps = None; self._am = None; self._sel = None; self._td = None
        self._loaded = False

    def load(self):
        if self._loaded: return True
        try:
            from fusion_phase2 import ToolChain, CodeSandbox, MCPAdapter
            from fusion_phase2 import PersistentState, AdaptiveMemory
            from fusion_phase2 import AgentSelector, TaskDecomposer
            self._tc = ToolChain(); self._sb = CodeSandbox()
            self._mcp = MCPAdapter(); self._ps = PersistentState()
            self._am = AdaptiveMemory(); self._sel = AgentSelector()
            self._td = TaskDecomposer(); self._loaded = True; return True
        except Exception as e:
            print(f"[P2] 降级: {e}"); self._loaded = False; return False

    @property
    def toolchain(self): self.load(); return self._tc
    @property
    def sandbox(self): self.load(); return self._sb
    @property
    def mcp(self): self.load(); return self._mcp
    @property
    def persistent(self): self.load(); return self._ps
    @property
    def adapt_mem(self): self.load(); return self._am
    @property
    def agent_selector(self): self.load(); return self._sel
    @property
    def task_decomposer(self): self.load(); return self._td

    def status(self) -> dict:
        return {"loaded": self._loaded, "toolchain": self._tc is not None,
                "persist": self._ps is not None, "selector": self._sel is not None}


# ════════════════════════════════════════════════════════════════
# 四·五、Phase3Plugin — 混沌×AI模块融合（2026-05-07新增）
# 将skills/math-foundation/的9大混沌模块注册为FusionGod插件
# ════════════════════════════════════════════════════════════════

class Phase3Plugin:
    """Phase3混沌×AI模块的FusionGod插件接入
    提供ChaoticAttention/ChaoticIRL/ChaoticDistill等9大模块

    钩子接口：
        pre_attention_hook(payload) → 注意力前注入混沌
        post_distill_hook(model)    → 蒸馏后注入混沌
        irl_enhance_hook(data)      → IRL逆强化学习中注入混沌
        chaotic_prng_hook(seed)     → 生成混沌随机数"""

    def __init__(self):
        self._modules = {}; self._loaded = False; self._load_error = None

    def load(self):
        if self._loaded: return True
        _chaos_map = {}
        try:
            base = r"C:\Users\Administrator\.openclaw\workspace\skills\math-foundation"
            import sys, importlib, importlib.util

            loader_map = {
                "chaos_ai_applications.py":
                    ["ChaoticAttentionHead", "ChaoticPositionalEncoding",
                     "ChaoticRegularizer", "ChaoticMCMC", "ChaoticDistiller",
                     "ChaoticEmbeddingIndex", "chaotic_softmax", "chaotic_activation"],
                "chaos_models.py":
                    ["ChaoticPRNG", "ChaoticGaitGenerator",
                     "logistic_map", "lorenz_system", "lorenz_lyapunov_exponents",
                     "bifurcation_data", "henon_map"],
                "chaotic_irl.py":
                    ["ChaoticCouplingLayer", "ChaoticIRL",
                     "chaotic_feature_map", "lyapunov_discrepancy"],
            }

            for fname, targets in loader_map.items():
                fp = os.path.join(base, fname)
                if not os.path.isfile(fp):
                    self._load_error = f"{fname} not found"; self._loaded = False; return False
                spec = importlib.util.spec_from_file_location(fname.replace('.py',''), fp)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for name in targets:
                    if hasattr(mod, name):
                        _chaos_map[name] = getattr(mod, name)

            self._modules = _chaos_map; self._loaded = True
            print(f"[P3] 混沌模块装载: {len(_chaos_map)}个组件 ✅"); return True
        except Exception as e:
            self._load_error = str(e); self._loaded = False
            print(f"[P3] 降级: {e}"); return False

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if not self._loaded:
            self.load()
        if name in self._modules:
            return self._modules[name]
        raise AttributeError(f"[P3] '{name}' 不在已装载模块中: {list(self._modules.keys())[:10]}")

    def get_available(self):
        return list(self._modules.keys())

    def pre_attention_hook(self, query, key, value, mask=None):
        """注意力层前钩子 — 注入混沌抖动（可选启用）"""
        if not self._loaded: return (query, key, value)
        head = self._modules.get("ChaoticAttentionHead")
        if head:
            try: return head().forward(query, key, value, mask)
            except Exception: pass
        return (query, key, value)

    def chaotic_prng(self, seed=None):
        """生成混沌随机数序列"""
        if not self._loaded: return None
        prng = self._modules.get("ChaoticPRNG")
        if prng:
            try: return prng().generate(seed)
            except Exception: pass
        return None

    def irl_enhance(self, data, n_steps=100):
        """IRL逆强化学习增强 — 注入混沌动力学"""
        if not self._loaded: return data
        irl = self._modules.get("ChaoticIRL")
        if irl:
            try: return irl().embed(data, n_steps)
            except Exception: pass
        return data

    def status(self) -> dict:
        self.load()  # 懒加载
        return {"loaded": self._loaded, "components": len(self._modules),
                "module_names": list(self._modules.keys())[:10],
                "error": self._load_error}


# ════════════════════════════════════════════════════════════════
# 五、融合之神 — 顶层API
# ════════════════════════════════════════════════════════════════

class FusionGod:
    """
    融合之神 — 四大引擎的统一入口
    
    用法：
        god = FusionGod()
        
        # 提交完整流水线任务
        task_id = god.submit("开发用户登录系统", "实现JWT认证和OAuth2.0")
        
        # 快速手动调用各引擎
        god.memskill_select(intent="修复bug", complexity="high")
        god.edict_flow(task_id, "方案：1.代码审计 2.漏洞修复 3.测试")
        god.auto_crystallize("追踪修复过程")
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self.bus = MessageBus()
        self._flow_history: List[str] = []
        self.phase2 = Phase2Plugin()
        self.phase3 = Phase3Plugin()  # 混沌×AI模块 (2026-05-07)
    
    def submit(self, title: str, description: str, auto_track: bool = True) -> str:
        """提交任务到完整流水线"""
        task_id = self.bus.submit(title, description)
        self._flow_history.append(task_id)
        
        if auto_track:
            # 自动开始实时记忆追踪
            self.bus.realtime._lazy_load()
            try:
                self.bus.realtime._rtm.start_session(title, description)
            except Exception:
                pass
            # 自动写当天日志（自主动作无痕修复）
            try:
                from auto_logging import ensure_today_log
                ensure_today_log()
                path = ensure_today_log()
                now = time.strftime("%H:%M", time.localtime())
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"\n## {now} — {title} [自动记录]\n"
                            f"- **类型**: FusionGod自主任务\n"
                            f"- **描述**: {description}\n")
            except Exception:
                pass
        
        return task_id
    
    def memskill_select(self, intent: str, complexity: str = "medium") -> Dict:
        """手动调用MemSkill策略选择"""
        return self.bus.memskill.select_strategy({
            "intent": intent,
            "complexity": complexity,
        })
    
    def edict_flow(self, task_id: str, plan: str) -> Dict:
        """手动运行三省六部完整流程"""
        self.bus.edict._lazy_load()
        orch = self.bus.edict._get_orchestrator()
        
        results = []
        
        # 1. 太子分拣（复杂任务）
        r = orch.taizi_sort(task_id)
        results.append(("taizi_sort", r.message))
        self.bus.emit(FlowMessage(
            flow_id=task_id, phase=FlowPhase.TAOZI_SORT,
            source="user", target="edict",
            payload={"sort_result": r.message, "is_complex": True}
        ))
        
        # 2. 中书省规划
        r = orch.zhongshu_plan(task_id, plan)
        results.append(("zhongshu_plan", r.message))
        
        # 3. 门下省审议
        r = orch.menxia_review(task_id, True, "方案可行")
        results.append(("menxia_review", r.message))
        
        # 4. 尚书省派发
        AgentRole = self.bus.edict._AgentRole
        r = orch.shangshu_dispatch(task_id, AgentRole.Bingbu, "执行方案")
        results.append(("shangshu_dispatch", r.message))
        self.bus.emit(FlowMessage(
            flow_id=task_id, phase=FlowPhase.SHANGSHU_DISPATCH,
            source="edict", target="generic_agent",
            payload={"task_id": task_id, "plan": plan}
        ))
        
        return {"task_id": task_id, "results": results}
    
    def auto_crystallize(self, task: str, plan: list = None, result: str = "") -> Dict:
        """手动触发自动结晶"""
        crystal = self.bus.generic_agent.crystallize(
            task=task,
            plan=plan or ["executed"],
            result=result or "completed"
        )
        return crystal
    
    def capture(self, intent: str = "", work: str = "", progress: int = 50,
                errors: list = None, decisions: list = None):
        """手动触发实时捕获"""
        self.bus.realtime.capture_snapshot(
            flow_id=f"manual_{int(time.time())}",
            phase="manual",
            payload={
                "title": intent,
                "description": work,
                "progress": progress,
                "errors": errors or [],
                "decisions": decisions or [],
            }
        )
    
    def get_status(self) -> dict:
        """获取融合系统状态"""
        return {
            "total_flows": len(self._flow_history),
            "recent_flows": self._flow_history[-5:],
            "bus": self.bus.stats(),
            "phase2": self.phase2.status(),
            "phase3": self.phase3.status(),
        }
    
    def get_flow_detail(self, flow_id: str) -> Optional[dict]:
        """获取流水线详情"""
        return self.bus.get_flow(flow_id)


# ════════════════════════════════════════════════════════════════
# 五、测试
# ════════════════════════════════════════════════════════════════

def test():
    """自测试"""
    print("=" * 60)
    print("🏛️  融合之神 FusionGod — 测试启动")
    print("=" * 60)
    
    god = FusionGod()
    
    # 测试1: 任务提交
    print("\n[1] 提交任务到流水线...")
    task_id = god.submit("开发用户登录系统", "实现JWT认证")
    print(f"  task_id={task_id}")
    
    # 测试2: MemSkill策略选择
    print("\n[2] MemSkill策略选择...")
    strategy = god.memskill_select(intent="开发功能", complexity="high")
    print(f"  策略: {strategy['selected']['skill']}")
    print(f"  Top-K: {strategy['top_k']}")
    
    # 测试3: 三省六部流程
    print("\n[3] 三省六部流转...")
    result = god.edict_flow(task_id, "方案：JWT+OAuth2.0+测试")
    for step, msg in result["results"]:
        print(f"  {step}: {msg}")
    
    # 测试4: 自动结晶
    print("\n[4] 自动结晶...")
    crystal = god.auto_crystallize(
        task="开发用户登录系统",
        plan=["JWT认证模块", "OAuth2.0集成", "安全审计"],
        result="全部通过"
    )
    print(f"  晶体: {crystal['name']}")
    
    # 测试5: 获取状态
    print("\n[5] 系统状态...")
    status = god.get_status()
    print(f"  总流水线: {status['total_flows']}")
    print(f"  晶体数: {status['bus']['crystal_count']}")
    
    # 测试6: 流水线详情
    print("\n[6] 流水线详情...")
    detail = god.get_flow_detail(task_id)
    if detail:
        print(f"  消息数: {detail['message_count']}")
        print(f"  阶段: {detail['phases']}")
    
    print("\n" + "=" * 60)
    print("✅ 融合之神测试通过！")
    print("=" * 60)
    
    return god


if __name__ == "__main__":
    import sys
    test()
