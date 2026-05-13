"""
实时记忆与反刍系统 (Real-time Memory & Rumination) — 050301-memory-v2

目标：将记忆能力从"被动记录"升级为"主动感知+自动沉淀+反刍进化"

三大升级：
1. 实时记忆拾取 (RealTimeMemory) — 每轮交互自动捕获：当前进度、错误教训、待办状态
2. 记忆分层晋升 (MemoryPromoter) — 短期→中期→长期→核心，按重要性自动升级
3. 反刍引擎 (RuminationEngine) — 空闲时自动回顾失败模式，提炼教训，阻止重复错误

数据结构：采用"快照+增量+快照"模型，避免单次交互的碎片化记录
"""

import json
import os
import time
import threading
import hashlib
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

# ============================================================
# 路径配置
# ============================================================
WS = r"C:\Users\Administrator\.openclaw\workspace"
MEMORY_DIR = os.path.join(WS, "memory")
RUMINATION_DIR = os.path.join(MEMORY_DIR, "rumination")
SNAPSHOT_DIR = os.path.join(RUMINATION_DIR, "snapshots")
PATTERNS_DIR = os.path.join(RUMINATION_DIR, "patterns")
PITFALLS_FILE = os.path.join(RUMINATION_DIR, "pitfalls.json")
PROGRESS_FILE = os.path.join(MEMORY_DIR, "progress.json")
SESSION_FILE = os.path.join(MEMORY_DIR, "session_state.json")

for d in [RUMINATION_DIR, SNAPSHOT_DIR, PATTERNS_DIR]:
    os.makedirs(d, exist_ok=True)

# ============================================================
# 一、实时记忆拾取 — 每轮交互后自动调用
# ============================================================

@dataclass
class InteractionSnapshot:
    """一次交互的快照 — 捕获全部关键信息"""
    timestamp: float
    session_id: str
    user_intent: str                  # 用户想要的最终结果
    current_work: str                 # 正在做什么
    progress_percent: int             # 估算进度 0-100
    unfinished_items: List[str]       # 还有哪些没做完
    errors_encountered: List[Dict]    # 遇到的错误 [{type, msg, lesson}]
    decisions_made: List[str]         # 做了哪些决策
    pending_blockers: List[str]       # 阻塞项（缺资源等）
    next_actions: List[str]           # 下一步做什么
    key_insights: List[str]           # 关键发现
    tags: List[str] = field(default_factory=list)

    def to_record(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "user_intent": self.user_intent,
            "current_work": self.current_work,
            "progress_percent": self.progress_percent,
            "unfinished_items": self.unfinished_items,
            "errors": self.errors_encountered,
            "decisions": self.decisions_made,
            "blockers": self.pending_blockers,
            "next_actions": self.next_actions,
            "insights": self.key_insights,
            "tags": self.tags,
            "human_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp)),
        }


class RealTimeMemory:
    """
    实时记忆拾取 — 自动捕获每轮交互的状态
    
    用法：
        rtm = RealTimeMemory()
        rtm.start_session("引才实时记忆")
        # ... 工作中 ...
        rtm.snapshot(
            user_intent="升级记忆系统",
            current_work="构建反刍引擎",
            progress=40,
            errors=[{"type":"import_error", "lesson":"需要先创建目录"}],
            next_actions=["完成反刍循环", "测试持久化"],
        )
        rtm.end_session(success=True, summary="记忆系统升级完成")
    """

    def __init__(self):
        self.current_session = None
        self.snapshot_count = 0
    
    def start_session(self, task_name: str, intent: str = ""):
        """开始新会话"""
        ts = time.time()
        session_id = f"ses_{hashlib.md5((task_name + str(ts)).encode()).hexdigest()[:8]}"
        self.current_session = {
            "session_id": session_id,
            "task_name": task_name,
            "intent": intent,
            "started_at": ts,
            "snapshots": [],
            "ended": False,
            "summary": "",
        }
        self.snapshot_count = 0
        self._persist_session()
        self._append_to_daily(f"📌 **开始任务**: {task_name}")
        return session_id
    
    def snapshot(self, user_intent: str = "", current_work: str = "",
                 progress: int = 0, errors: list = None,
                 decisions: list = None, blockers: list = None,
                 next_actions: list = None, insights: list = None,
                 unfinished: list = None):
        """捕获当前状态快照"""
        self.snapshot_count += 1
        snap = InteractionSnapshot(
            timestamp=time.time(),
            session_id=self.current_session["session_id"] if self.current_session else "",
            user_intent=user_intent or self.current_session.get("intent", ""),
            current_work=current_work,
            progress_percent=min(progress, 100),
            unfinished_items=unfinished or [],
            errors_encountered=errors or [],
            decisions_made=decisions or [],
            pending_blockers=blockers or [],
            next_actions=next_actions or [],
            key_insights=insights or [],
        )
        if self.current_session:
            self.current_session["snapshots"].append(snap.to_record())
            self._persist_session()
        
        # 错误自动入坑池
        for err in (errors or []):
            self._record_pitfall({
                "type": err.get("type", "unknown"),
                "msg": err.get("msg", ""),
                "lesson": err.get("lesson", ""),
                "context": current_work,
                "timestamp": time.time(),
                "session": self.current_session["task_name"] if self.current_session else "",
            })
        
        # 未完成事项记录
        for item in (unfinished or []):
            self._append_to_progress({
                "type": "unfinished",
                "item": item,
                "session": self.current_session["task_name"] if self.current_session else "",
                "timestamp": time.time(),
            })
        
        # 洞察自动沉淀到长期记忆
        for ins in (insights or []):
            self._append_to_daily(f"💡 **洞察**: {ins}")
        
        # 写到当天日志
        log_lines = []
        if current_work:
            log_lines.append(f"⚙️ **工作中**: {current_work} | 进度: {progress}%")
        for d in (decisions or []):
            log_lines.append(f"🔑 **决策**: {d}")
        for b in (blockers or []):
            log_lines.append(f"🚧 **阻塞**: {b}")
        for a in (next_actions or []):
            log_lines.append(f"➡️ **下一步**: {a}")
        for u in (unfinished or []):
            log_lines.append(f"📋 **未完成**: {u}")
        
        if log_lines:
            self._append_to_daily("\n".join(log_lines))
        
        return snap
    
    def end_session(self, success: bool, summary: str = ""):
        """结束会话，生成总结"""
        if not self.current_session:
            return
        self.current_session["ended"] = True
        self.current_session["success"] = success
        self.current_session["summary"] = summary
        self.current_session["snapshot_count"] = self.snapshot_count
        self._persist_session()
        
        # 写入每日日志
        status = "✅ 完成" if success else "❌ 失败/中断"
        self._append_to_daily(f"\n---\n📊 **任务结束**: {self.current_session['task_name']} | {status}\n> {summary}\n---\n")
        
        # 存入progress.json作为已完成事项
        self._append_to_progress({
            "type": "completed" if success else "abandoned",
            "task": self.current_session["task_name"],
            "summary": summary,
            "snapshots": self.snapshot_count,
            "timestamp": time.time(),
        })
        
        self.current_session = None
    
    def record_error(self, error_type: str, message: str, lesson: str, context: str = ""):
        """快速记录一个错误（不创建完整snapshot）"""
        self._record_pitfall({
            "type": error_type,
            "msg": message,
            "lesson": lesson,
            "context": context or self.current_session.get("task_name", "") if self.current_session else "",
            "timestamp": time.time(),
            "session": self.current_session.get("task_name", "") if self.current_session else "",
        })
        self._append_to_daily(f"⚠️ **错误**: [{error_type}] {message}\n> 教训: {lesson}")
    
    def note_blocker(self, blocker: str, resolution: str = ""):
        """记录阻塞项及解决方案"""
        entry = {
            "blocker": blocker,
            "resolution": resolution,
            "timestamp": time.time(),
            "session": self.current_session["task_name"] if self.current_session else "",
        }
        self._append_to_progress({"type": "blocker", **entry})
        if resolution:
            self._append_to_daily(f"🚧 **阻塞已解决**: {blocker} → {resolution}")
        else:
            self._append_to_daily(f"🚧 **新阻塞**: {blocker}")
    
    def note_decision(self, decision: str, rationale: str = ""):
        """记录决策"""
        entry = {"decision": decision, "rationale": rationale, "timestamp": time.time()}
        self._append_to_progress({"type": "decision", **entry})
        self._append_to_daily(f"🔑 **决策**: {decision}" + (f" | 理由: {rationale}" if rationale else ""))
    
    # ── 持久化方法 ──
    
    def _persist_session(self):
        """持久化当前会话"""
        if not self.current_session:
            return
        path = os.path.join(SNAPSHOT_DIR, f"{self.current_session['session_id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.current_session, f, ensure_ascii=False, indent=2)
    
    def _record_pitfall(self, pitfall: dict):
        """记录失败教训"""
        pitfalls = self._load_json(PITFALLS_FILE, [])
        # 去重：相同type+msg+context不重复记录
        for p in pitfalls:
            if (p.get("type") == pitfall.get("type") and
                p.get("msg") == pitfall.get("msg") and
                p.get("context") == pitfall.get("context")):
                p["occurrences"] = p.get("occurrences", 1) + 1
                p["last_seen"] = time.time()
                self._save_json(PITFALLS_FILE, pitfalls)
                return
        pitfall["occurrences"] = 1
        pitfall["first_seen"] = time.time()
        pitfall["last_seen"] = time.time()
        pitfalls.append(pitfall)
        self._save_json(PITFALLS_FILE, pitfalls)
    
    def _append_to_daily(self, content: str):
        """追加到当天日志"""
        today = time.strftime("%Y-%m-%d", time.localtime())
        path = os.path.join(MEMORY_DIR, f"{today}.md")
        ts = time.strftime("%H:%M", time.localtime())
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n> [{ts}] {content}\n")
    
    def _append_to_progress(self, entry: dict):
        """记录进度事项"""
        progress = self._load_json(PROGRESS_FILE, {"items": [], "count": 0})
        progress["items"].append(entry)
        progress["count"] += 1
        # 只保留最近200条
        if len(progress["items"]) > 200:
            progress["items"] = progress["items"][-200:]
        self._save_json(PROGRESS_FILE, progress)
    
    @staticmethod
    def _load_json(path: str, default):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return default
        return default
    
    @staticmethod
    def _save_json(path: str, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 二、记忆晋升器 — 从短期沉淀到长期
# ============================================================

class MemoryPromoter:
    """
    记忆分层晋升器
    
    机制：
    - 每条记忆有 score = importance * freshness
    - 初始存储在 short_term (工作级)
    - score > 阈值 → promote 到 long_term
    - long_term 满时，淘汰最低 score
    - 核心记忆 (core) 永不淘汰，手动设置
    """

    def __init__(self, memory_dir: str = MEMORY_DIR):
        self.memory_dir = memory_dir
        self.core_memory_path = os.path.join(memory_dir, "CORE.md")
        self.short_term: List[Dict] = []
        self.long_term: List[Dict] = []
        self._load()
    
    def add(self, content: str, importance: float = 0.5, tags: list = None,
            source: str = "", is_core: bool = False):
        """添加一条记忆，自动判断层级"""
        mem = {
            "content": content,
            "importance": importance,
            "tags": tags or [],
            "source": source,
            "created": time.time(),
            "accessed": time.time(),
            "access_count": 0,
            "promoted": False,
        }
        if is_core:
            self._append_core(content)
            return
        
        self.short_term.append(mem)
        self._trim_short_term()
        self._try_promote(mem)
        self._save()
    
    def promote_all(self, min_importance: float = 0.6):
        """批量晋升到长期记忆"""
        promoted = []
        remaining = []
        for mem in self.short_term:
            if mem["importance"] >= min_importance:
                mem["promoted"] = True
                promoted.append(mem)
            else:
                remaining.append(mem)
        
        self.short_term = remaining
        self.long_term.extend(promoted)
        self._trim_long_term()
        self._save()
        
        # 同时写入CORE.md里最重要的
        for mem in promoted[:3]:
            self._append_core(mem["content"])
        
        return len(promoted)
    
    def get_relevant(self, keywords: list, top_k: int = 5) -> List[Dict]:
        """检索相关记忆（语义匹配+重要性加权）"""
        results = []
        for mem in self.long_term + self.short_term:
            score = 0
            for kw in keywords:
                if kw.lower() in mem["content"].lower():
                    score += 1
            if score > 0:
                score *= mem["importance"]
                results.append((score, mem))
        results.sort(key=lambda x: -x[0])
        return [m for _, m in results[:top_k]]
    
    def get_core(self) -> str:
        """读取CORE记忆"""
        if os.path.exists(self.core_memory_path):
            with open(self.core_memory_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""
    
    def _append_core(self, content: str):
        """追加到CORE.md"""
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime())
        with open(self.core_memory_path, "a", encoding="utf-8") as f:
            f.write(f"\n- [{ts}] {content}")
    
    def _try_promote(self, mem: Dict):
        """尝试晋升单条记忆"""
        freshness = 1.0 / (1.0 + (time.time() - mem["created"]) / 3600)
        score = mem["importance"] * freshness
        if score > 1.5:  # 晋升阈值
            self.short_term.remove(mem) if mem in self.short_term else None
            mem["promoted"] = True
            self.long_term.append(mem)
            self._trim_long_term()
    
    def _trim_short_term(self):
        if len(self.short_term) > 50:
            self.short_term.sort(key=lambda x: x["importance"])
            self.short_term = self.short_term[-50:]
    
    def _trim_long_term(self):
        if len(self.long_term) > 200:
            self.long_term.sort(key=lambda x: x["importance"] * x["access_count"])
            self.long_term = self.long_term[-200:]
    
    def _save(self):
        path = os.path.join(self.memory_dir, "promoted_memory.json")
        data = {
            "short_term": self.short_term,
            "long_term": self.long_term,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load(self):
        path = os.path.join(self.memory_dir, "promoted_memory.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.short_term = data.get("short_term", [])
                self.long_term = data.get("long_term", [])
            except:
                pass


# ============================================================
# 三、反刍引擎 — 空闲时自动回顾
# ============================================================

class RuminationEngine:
    """
    反刍引擎 — 周期性回顾失败教训，提炼模式，阻止重复错误
    
    核心流程：
    pitfall池 → 聚类 → 模式识别 → 模式抽象 → 写入禁止规则
                                 ↓
                           heartbeat时主动提醒
    """

    def __init__(self):
        self.pitfalls = self._load_pitfalls()
        self.patterns: List[Dict] = []
        self._load_patterns()
    
    def ruminate(self) -> Dict[str, Any]:
        """
        执行一次反刍：分析所有失败教训，提炼模式
        
        Returns:
            {"new_patterns": int, "total_pitfalls": int, "recommendations": [...]}
        """
        if not self.pitfalls:
            return {"new_patterns": 0, "total_pitfalls": 0, "recommendations": ["无失败记录，继续保持"]}
        
        # 1. 按失败类型聚类
        clusters = defaultdict(list)
        for p in self.pitfalls:
            clusters[p.get("type", "unknown")].append(p)
        
        # 2. 每个聚类提取模式
        new_patterns = 0
        recommendations = []
        
        for error_type, items in sorted(clusters.items(), key=lambda x: -len(x[1])):
            occurrences = sum(i.get("occurrences", 1) for i in items)
            unique_contexts = len(set(i.get("context", "") for i in items))
            
            # 高频重复错误 — 需要立即关注
            if occurrences >= 3 or unique_contexts >= 2:
                # 检查是否已有该模式
                if not self._has_pattern(error_type):
                    # 提取教训
                    lessons = [i.get("lesson", "") for i in items if i.get("lesson")]
                    most_common_lesson = max(set(lessons), key=lessons.count) if lessons else "无明确教训"
                    
                    pattern = {
                        "type": error_type,
                        "occurrences": occurrences,
                        "contexts": unique_contexts,
                        "abstracted_lesson": most_common_lesson,
                        "first_seen": min(i.get("timestamp", time.time()) for i in items),
                        "last_seen": max(i.get("timestamp", time.time()) for i in items),
                        "rule": self._generate_rule(error_type, most_common_lesson),
                        "created": time.time(),
                    }
                    self.patterns.append(pattern)
                    new_patterns += 1
                    
                    recommendations.append(
                        f"[{error_type}] 出现{occurrences}次，在{unique_contexts}个场景: "
                        f"{most_common_lesson}"
                    )
        
        self._save_patterns()
        
        # 3. 生成反刍报告
        report = {
            "new_patterns": new_patterns,
            "total_pitfalls": len(self.pitfalls),
            "total_patterns": len(self.patterns),
            "recommendations": recommendations[:5],  # top5
            "ruminated_at": time.time(),
            "summary": self._generate_summary(recommendations),
        }
        self._save_rumination_report(report)
        
        return report
    
    def get_active_warnings(self) -> List[str]:
        """获取当前活跃的警告（用于heartbeat检查）"""
        warnings = []
        for p in self.patterns:
            hours_since = (time.time() - p.get("last_seen", 0)) / 3600
            if hours_since < 24 and p.get("occurrences", 0) >= 3:
                warnings.append(p.get("abstracted_lesson", ""))
        return warnings
    
    def get_blocked_actions(self) -> List[Dict]:
        """获取应避免的重复操作（供编码时参考）"""
        rules = []
        for p in self.patterns:
            if p.get("occurrences", 0) >= 2:
                rules.append({
                    "rule": p.get("rule", ""),
                    "lesson": p.get("abstracted_lesson", ""),
                    "seen_times": p.get("occurrences", 0),
                })
        return rules
    
    def _has_pattern(self, error_type: str) -> bool:
        return any(p.get("type") == error_type for p in self.patterns)
    
    def _generate_rule(self, error_type: str, lesson: str) -> str:
        """从错误教训生成可执行的禁止规则"""
        type_to_rule = {
            "import_error": "导入前先检查模块是否已安装，未安装则先pip install",
            "file_not_found": "读文件前先用os.path.exists检查路径存在性",
            "path_error": "路径拼接用os.path.join而非手动拼接字符串",
            "encoding_error": "打开文件时始终指定encoding='utf-8'",
            "permission_error": "写文件前确保目标目录存在，用os.makedirs(exist_ok=True)",
            "network_error": "网络操作前先检查连通性，超时设为30s并捕获异常",
            "memory_error": "大数据操作分批处理，避免一次性加载全部到内存",
            "api_limit_error": "API调用间隔至少1s，使用指数退避重试机制",
            "json_decode_error": "加载JSON前用try/except捕获，提供默认值",
            "key_error": "字典取值始终用.get(key, default)而非dict[key]",
        }
        # 模糊匹配
        for key, rule in type_to_rule.items():
            if key in error_type.lower() or error_type.lower() in key:
                return rule
        return f"注意避免重复错误: {lesson[:50]}"
    
    def _generate_summary(self, recommendations: list) -> str:
        if not recommendations:
            return "本次反刍未发现新的失败模式"
        return f"发现{len(recommendations)}条可提炼模式: " + "; ".join(recommendations[:3])
    
    def _load_pitfalls(self) -> list:
        if os.path.exists(PITFALLS_FILE):
            try:
                with open(PITFALLS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return []
        return []
    
    def _load_patterns(self):
        pattern_file = os.path.join(PATTERNS_DIR, "patterns.json")
        if os.path.exists(pattern_file):
            try:
                with open(pattern_file, "r", encoding="utf-8") as f:
                    self.patterns = json.load(f)
            except:
                pass
    
    def _save_patterns(self):
        with open(os.path.join(PATTERNS_DIR, "patterns.json"), "w", encoding="utf-8") as f:
            json.dump(self.patterns, f, ensure_ascii=False, indent=2)
    
    def _save_rumination_report(self, report: dict):
        ts = time.strftime("%Y%m%d_%H%M", time.localtime())
        path = os.path.join(RUMINATION_DIR, f"rumination_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)


# ============================================================
# 四、集成入口 (GlobalHooks)
# ============================================================

class MemorySystem:
    """
    全局记忆系统 — 集成 RealTimeMemory + MemoryPromoter + RuminationEngine
    
    每轮交互自动触发：
    1. snapshot() → 捕获当前状态
    2. promote() → 短文转长文
    3. (可选) ruminate() → 空闲时反刍
    
    使用：
        mem = MemorySystem()
        mem.start("构建实时记忆系统")
        # ...工作中...
        mem.snapshot(progress=50, current_work="写持久化层", ...)
        # ...出错时...
        mem.error("import_error", "模块X未安装", "需要先pip install")
        # ...完成后...
        mem.end(success=True, summary="完成了XYZ")
        
        # 空闲时
        report = mem.ruminate()  # 反刍
        warnings = mem.warnings()  # 获取当前警告
    """

    def __init__(self):
        self.real_time = RealTimeMemory()
        self.promoter = MemoryPromoter()
        self.rumination = RuminationEngine()
        self._last_rumination = 0
    
    def start(self, task_name: str, intent: str = ""):
        """开始新任务"""
        return self.real_time.start_session(task_name, intent)
    
    def snapshot(self, **kwargs):
        """捕获状态快照"""
        result = self.real_time.snapshot(**kwargs)
        
        # 自动晋升：遇到洞察时
        insights = kwargs.get("insights", [])
        for ins in insights:
            self.promoter.add(ins, importance=0.7, tags=["insight"],
                            source=kwargs.get("current_work", ""))
        
        # 自动晋升：决策
        decisions = kwargs.get("decisions", [])
        for dec in decisions:
            self.promoter.add(f"决策: {dec}", importance=0.6, tags=["decision"])
        
        return result
    
    def error(self, error_type: str, message: str, lesson: str, context: str = ""):
        """记录错误"""
        self.real_time.record_error(error_type, message, lesson, context)
        
        # 错误也加入短期记忆
        self.promoter.add(
            f"教训: [{error_type}] {lesson}",
            importance=0.8,
            tags=["error", error_type],
            source=context,
        )
        
        # 如果同类错误已多次出现，触发反刍
        current_pitfalls = self.real_time._load_json(PITFALLS_FILE, [])
        similar = [p for p in current_pitfalls if p.get("type") == error_type]
        if len(similar) >= 3:
            self.ruminate()  # 自动反刍
    
    def decision(self, decision: str, rationale: str = ""):
        """记录决策"""
        self.real_time.note_decision(decision, rationale)
        self.promoter.add(
            f"决策: {decision}" + (f" ({rationale})" if rationale else ""),
            importance=0.6, tags=["decision"])
    
    def blocker(self, blocker: str, resolution: str = ""):
        """记录阻塞"""
        self.real_time.note_blocker(blocker, resolution)
    
    def end(self, success: bool, summary: str = ""):
        """结束任务"""
        self.real_time.end_session(success, summary)
        # 任务结束后自动晋升一批记忆
        promoted = self.promoter.promote_all(min_importance=0.6)
        return {"promoted": promoted}
    
    def ruminate(self) -> Dict[str, Any]:
        """执行一次反刍"""
        self._last_rumination = time.time()
        report = self.rumination.ruminate()
        # 反刍结果也加入长期记忆
        for rec in report.get("recommendations", [])[:3]:
            self.promoter.add(
                f"反刍: {rec}",
                importance=0.9,
                tags=["rumination", "pattern"],
            )
        return report
    
    def warnings(self) -> List[str]:
        """获取当前活跃警告"""
        return self.rumination.get_active_warnings()
    
    def blocked_actions(self) -> List[Dict]:
        """获取应避免的操作"""
        return self.rumination.get_blocked_actions()
    
    def get_status(self) -> Dict:
        """系统状态"""
        pitfalls = self.real_time._load_json(PITFALLS_FILE, [])
        return {
            "pitfalls_total": len(pitfalls),
            "patterns_total": len(self.rumination.patterns),
            "short_term": len(self.promoter.short_term),
            "long_term": len(self.promoter.long_term),
            "last_rumination": self._last_rumination,
            "active_sessions": sum(1 for f in os.listdir(SNAPSHOT_DIR) 
                                  if f.endswith(".json"))
        }


# ============================================================
# 五、自检
# ============================================================

if __name__ == "__main__":
    import sys
    
    # Force UTF-8 for stdout
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("=" * 60)
    print("[REALTIME MEMORY] === 实时记忆与反刍系统 自检 ===")
    print("=" * 60)
    
    # 1. 初始化
    mem = MemorySystem()
    print(f"\n[1/5] 初始化 [OK]")
    
    # 2. 模拟实时记录
    sid = mem.start("记忆系统升级", intent="添加实时记忆和反刍能力")
    print(f"[2/5] 启动会话: {sid} [OK]")
    
    # 模拟工作中遇到错误
    mem.error("import_error", "pandas未安装", "使用前先pip install pandas", "数据导入")
    mem.error("file_not_found", "config.json不存在", "先os.path.exists检查", "配置加载")
    mem.error("import_error", "tkinter未安装", "使用前检查系统组件", "GUI初始化")
    
    # 模拟快照
    mem.snapshot(
        current_work="构建反刍引擎",
        progress=40,
        decisions=["采用快照+增量模式", "数据库改为JSON持久化"],
        insights=["去重错误教训比收集更重要", "反刍应该在任务间隙而非任务中执行"],
        next_actions=["完成反刍循环", "编写测试", "集成到本体"],
        unfinished=["持久化层测试", "自动调用hook"],
        blockers=["需要确认文件路径是否存在"],
    )
    
    # 模拟决策
    mem.decision("选择JSON而非SQLite作为存储", "轻量、无需额外依赖、易于调试")
    
    # 模拟结束
    result = mem.end(True, "实时记忆系统构建完成，含快照/反刍/晋升三层")
    print(f"[3/5] 任务结束 - 晋升了{result['promoted']}条记忆 [OK]")
    
    # 3. 反刍
    report = mem.ruminate()
    print(f"[4/5] 反刍结果: {report['new_patterns']}条新模式, {report['total_patterns']}条总计 [OK]")
    for rec in report.get("recommendations", []):
        print(f"  [!] {rec}")
    
    # 4. 主动警告
    warnings = mem.warnings()
    print(f"[5/5] 活跃警告: {len(warnings)}条")
    for w in warnings:
        print(f"  [!!] {w}")
    
    # 5. 状态
    status = mem.get_status()
    print(f"\n=== 系统状态 ===")
    for k, v in status.items():
        print(f"  {k}: {v}")
    
    print(f"\n[OK] 实时记忆与反刍系统自检完成!")
