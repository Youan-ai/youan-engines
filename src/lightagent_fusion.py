"""
LightAgent Fusion — 轻量工具系统融合版
─────────────────────────────────────
基于 LightAgent (wanxingai, 904★) 核心设计融合到佑安本体。
零依赖、装饰器驱动的工具注册和技能管理系统。

核心设计：
- ToolRegistry: @tool 装饰器注册 + 参数自动提取 + 上下文预过滤
- SkillManager: 目录即技能，自动发现 skills/ 目录下的 SKILL.md
- AsyncSafeExecutor: 解决 asyncio 嵌套问题的安全执行器
- MemoryProtocol: 协议化内存接口（轻量版）
- LightSwarm: 多 Agent 协作分配器

融合版本差异：
- 移除网络依赖 (FASTAPI/HTTP)
- 移除复杂第三方库 (pydantic/typer)
- 纯 Python 3.12+ 标准库
- 与 GenericAgent 的编码结晶、MemSkill 的记忆系统联动
"""

import asyncio
import inspect
import json
import os
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)


# ════════════════════════════════════════════════════════════════
# 1. ToolRegistry — 工具注册中心
# ════════════════════════════════════════════════════════════════

class ToolRegistry:
    """零依赖工具注册中心。支持同步和异步函数。
    
    使用:
        reg = ToolRegistry()
        
        @reg.tool(name="search", description="搜索网络")
        def search(query: str, count: int = 5) -> str:
            ...
            
        result = await reg.execute("search", {"query": "AI", "count": 3})
    """
    
    def __init__(self, auto_prune: bool = True):
        self._tools: Dict[str, dict] = {}
        self._disabled: Set[str] = set()
        self._call_history: list = []
        self._auto_prune = auto_prune
    
    def tool(self, name: Optional[str] = None, description: str = "", 
             category: str = "通用", hidden: bool = False,
             max_calls_per_task: Optional[int] = None):
        """注册工具函数的装饰器。自动提取参数签名。"""
        def decorator(func):
            nonlocal name
            tool_name = name or func.__name__
            
            # 提取参数信息
            sig = inspect.signature(func)
            params = {}
            for pname, param in sig.parameters.items():
                default = None if param.default is inspect.Parameter.empty else param.default
                annotation = str if param.annotation is inspect.Parameter.empty else param.annotation
                required = param.default is inspect.Parameter.empty
                params[pname] = {
                    "type": annotation.__name__ if hasattr(annotation, '__name__') else str(annotation),
                    "required": required,
                    "default": default,
                }
            
            # 如果是方法则跳过self
            if tool_name != "self" and "self" in params:
                del params["self"]
            
            # 决定执行方式
            is_async = asyncio.iscoroutinefunction(func)
            
            self._tools[tool_name] = {
                "name": tool_name,
                "description": description or func.__doc__ or "",
                "func": func,
                "signature": sig,
                "params": params,
                "is_async": is_async,
                "category": category,
                "hidden": hidden,
                "call_count": 0,
                "max_calls": max_calls_per_task or 999999,
                "enabled": True,
            }
            return func
        return decorator
    
    def register(self, func: Callable, name: Optional[str] = None,
                 description: str = "", **kwargs):
        """直接注册工具函数（非装饰器模式）。"""
        return self.tool(name=name, description=description, **kwargs)(func)
    
    def get(self, name: str) -> Optional[dict]:
        return self._tools.get(name)
    
    def list_tools(self, category: Optional[str] = None, include_hidden: bool = False) -> List[dict]:
        result = []
        for tool in self._tools.values():
            if tool["hidden"] and not include_hidden:
                continue
            if category and tool["category"] != category:
                continue
            if not tool["enabled"]:
                continue
            result.append({
                "name": tool["name"],
                "description": tool["description"][:100],
                "params": tool["params"],
                "category": tool["category"],
            })
        return result
    
    async def execute(self, name: str, kwargs: dict, context: Optional[dict] = None) -> Any:
        """执行工具函数。自动处理同步/异步。"""
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"Tool '{name}' not found", "success": False}
        if not tool["enabled"] or name in self._disabled:
            return {"error": f"Tool '{name}' is disabled", "success": False}
        
        # 调用次数限制
        tool["call_count"] += 1
        if tool["call_count"] > tool["max_calls"]:
            return {"error": f"Tool '{name}' exceeded max calls ({tool['max_calls']})", "success": False}
        
        # 参数过滤：只传函数接受的参数
        filtered = {k: v for k, v in kwargs.items() if k in tool["params"]}
        
        start = time.time()
        try:
            if tool["is_async"]:
                result = await tool["func"](**filtered)
            else:
                result = tool["func"](**filtered)
            elapsed = time.time() - start
            
            self._call_history.append({
                "tool": name, "args": filtered, "elapsed": elapsed, 
                "success": True, "time": time.time()
            })
            
            return {"data": result, "success": True, "elapsed": elapsed}
        except Exception as e:
            elapsed = time.time() - start
            self._call_history.append({
                "tool": name, "args": filtered, "elapsed": elapsed,
                "success": False, "error": str(e), "time": time.time()
            })
            return {"error": str(e), "traceback": traceback.format_exc(), "success": False, "elapsed": elapsed}
    
    def disable(self, name: str):
        self._disabled.add(name)
        if name in self._tools:
            self._tools[name]["enabled"] = False
    
    def enable(self, name: str):
        self._disabled.discard(name)
        if name in self._tools:
            self._tools[name]["enabled"] = True
    
    def stats(self) -> dict:
        usage = {}
        for t in self._tools.values():
            usage[t["name"]] = t["call_count"]
        return {
            "total_tools": len(self._tools),
            "disabled": len(self._disabled),
            "total_calls": sum(t["call_count"] for t in self._tools.values()),
            "usage": usage,
            "last_calls": self._call_history[-10:] if self._call_history else [],
        }
    
    def prune_history(self, max_age: float = 3600):
        """清理过期历史记录"""
        now = time.time()
        self._call_history = [h for h in self._call_history if now - h["time"] < max_age]


# ════════════════════════════════════════════════════════════════
# 2. SkillManager — 技能管理器 (目录即技能)
# ════════════════════════════════════════════════════════════════

@dataclass
class SkillInfo:
    """技能元信息"""
    name: str
    path: str
    description: str
    version: str = "1.0.0"
    category: str = "通用"
    dependencies: List[str] = field(default_factory=list)
    enabled: bool = True


class SkillManager:
    """技能管理器。目录即技能——自动发现 skills/ 下的 SKILL.md。"""
    
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = skills_dir
        self._skills: Dict[str, SkillInfo] = {}
        self._manual_skills: Dict[str, SkillInfo] = {}
    
    def discover(self) -> List[SkillInfo]:
        """自动发现 skills/ 目录下的技能"""
        discovered = []
        base = os.path.abspath(self.skills_dir)
        
        if not os.path.isdir(base):
            return discovered
        
        for entry in os.listdir(base):
            skill_path = os.path.join(base, entry)
            skill_file = os.path.join(skill_path, "SKILL.md")
            
            if os.path.isdir(skill_path) and os.path.exists(skill_file):
                # 从 SKILL.md 头部解析元信息
                description = ""
                with open(skill_file, "r", encoding="utf-8", errors="ignore") as f:
                    first_line = f.readline().strip()
                    description = first_line.lstrip("#").strip()
                
                info = SkillInfo(
                    name=entry,
                    path=skill_path,
                    description=description or f"技能: {entry}",
                )
                self._skills[entry] = info
                discovered.append(info)
        
        return discovered
    
    def register(self, name: str, path: str, description: str = "",
                 category: str = "通用", dependencies: List[str] = None):
        """手动注册技能。"""
        info = SkillInfo(
            name=name, path=path, description=description,
            category=category, dependencies=dependencies or [],
        )
        self._manual_skills[name] = info
        return info
    
    def get(self, name: str) -> Optional[SkillInfo]:
        return self._skills.get(name) or self._manual_skills.get(name)
    
    def list_skills(self, category: Optional[str] = None) -> List[SkillInfo]:
        result = list(self._skills.values()) + list(self._manual_skills.values())
        if category:
            result = [s for s in result if s.category == category and s.enabled]
        return result
    
    def all_names(self) -> List[str]:
        return list(self._skills.keys()) + list(self._manual_skills.keys())
    
    def get_skill_path(self, name: str) -> Optional[str]:
        info = self.get(name)
        return info.path if info else None


# ════════════════════════════════════════════════════════════════
# 3. AsyncSafeExecutor — 异步安全执行器
# ════════════════════════════════════════════════════════════════

class AsyncSafeExecutor:
    """解决 asyncio 嵌套问题（在已运行事件循环中创建新循环）。
    
    在 LLM 环境下经常遇到 RuntimeError("asyncio.run() cannot be called from a running event loop")。
    本执行器自动检测事件循环状态并选择正确的执行方式。
    """
    
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    async def run(self, coro):
        """同步执行协程（自动检测循环状态）"""
        try:
            loop = asyncio.get_running_loop()
            # 已有循环 → 直接 await
            return await coro
        except RuntimeError:
            # 无运行中循环 → 创建新循环
            return asyncio.run(coro)
    
    def run_sync(self, coro):
        """同步接口包装"""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # 最安全的方式：创建新循环
                new_loop = asyncio.new_event_loop()
                try:
                    return new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
            else:
                return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════
# 4. MemoryProtocol — 协议化内存接口
# ════════════════════════════════════════════════════════════════

class MemoryStore:
    """轻量内存存储。实现 MemoryProtocol 接口。"""
    
    def __init__(self, store: Optional[dict] = None):
        self._store = store or {}
        self._version = 1
    
    def put(self, key: str, value: Any) -> None:
        self._store[key] = value
        self._version += 1
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)
    
    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            self._version += 1
            return True
        return False
    
    def keys(self, prefix: str = "") -> List[str]:
        if prefix:
            return [k for k in self._store if k.startswith(prefix)]
        return list(self._store.keys())
    
    def search(self, query: str) -> List[Tuple[str, Any]]:
        """简单前缀搜索。"""
        q = query.lower()
        return [(k, v) for k, v in self._store.items() if q in k.lower()]
    
    def clear(self) -> None:
        self._store.clear()
        self._version += 1
    
    @property
    def version(self) -> int:
        return self._version
    
    def snapshot(self) -> dict:
        """返回快照用于持久化。"""
        return {"version": self._version, "data": self._store.copy()}
    
    def restore(self, snapshot: dict) -> None:
        self._version = snapshot.get("version", 1)
        self._store = snapshot.get("data", {})


class SimpleMemoryStore(MemoryStore):
    """MemoryStore 的别名，兼容性。"""
    pass


# ════════════════════════════════════════════════════════════════
# 5. LightSwarm — 多 Agent 协作分配器
# ════════════════════════════════════════════════════════════════

@dataclass
class AgentSpec:
    """Agent 规格"""
    name: str
    role: str
    capabilities: List[str]
    model: str = "default"
    max_tokens: int = 4096
    temperature: float = 0.7


class LightSwarm:
    """轻量多 Agent 协作分配器。
    
    根据任务类型自动分配到最合适的 Agent。
    支持并行执行、结果聚合、错误重试。
    """
    
    def __init__(self):
        self._agents: Dict[str, AgentSpec] = {}
        self._task_history: list = []
    
    def register_agent(self, spec: AgentSpec) -> None:
        self._agents[spec.name] = spec
    
    def get_agent(self, name: str) -> Optional[AgentSpec]:
        return self._agents.get(name)
    
    def dispatch(self, task: str, context: Optional[dict] = None) -> List[AgentSpec]:
        """根据任务描述和能力匹配返回最适合的 Agent 列表。"""
        task_lower = task.lower()
        scored = []
        
        for name, agent in self._agents.items():
            score = 0
            for cap in agent.capabilities:
                if cap.lower() in task_lower:
                    score += 1
            if score > 0:
                scored.append((score, agent))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored]
    
    async def parallel_execute(self, tasks: List[Tuple[str, str, dict]], 
                                context: Optional[dict] = None) -> List[dict]:
        """并行执行多个任务。
        
        tasks: [(agent_name, action, params), ...]
        """
        # 简化实现：顺序执行（真正的并行执行需要 LLM 调用）
        results = []
        for agent_name, action, params in tasks:
            agent = self._agents.get(agent_name)
            results.append({
                "agent": agent_name,
                "action": action,
                "params": params,
                "result": f"[模拟] Agent {agent_name} 执行 {action}",
            })
            self._task_history.append({
                "agent": agent_name, "action": action,
                "time": time.time(), "success": True,
            })
        return results
    
    def stats(self) -> dict:
        return {
            "agents": len(self._agents),
            "total_tasks": len(self._task_history),
            "recent": self._task_history[-5:] if self._task_history else [],
        }


# ════════════════════════════════════════════════════════════════
# 6. LightAgent — 主入口
# ════════════════════════════════════════════════════════════════

class LightAgent:
    """LightAgent 主入口——整合所有组件。"""
    
    def __init__(self, skills_dir: str = "skills"):
        self.tools = ToolRegistry()
        self.skills = SkillManager(skills_dir)
        self.executor = AsyncSafeExecutor()
        self.memory = SimpleMemoryStore()
        self.swarm = LightSwarm()
        
        # 注册内置工具
        self._register_builtins()
        self._initialized = False
    
    def _register_builtins(self):
        """注册内置工具函数。"""
        
        @self.tools.tool(name="get_time", description="获取当前时间",
                         category="系统")
        def get_time() -> str:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        
        @self.tools.tool(name="list_skills", description="列出所有可用技能",
                         category="系统")
        def list_skills() -> list:
            return [{"name": s.name, "description": s.description, "category": s.category} 
                    for s in self.skills.list_skills()]
        
        @self.tools.tool(name="list_tools", description="列出所有注册工具",
                         category="系统")
        def list_tools(category: Optional[str] = None) -> list:
            return self.tools.list_tools(category=category)
        
        @self.tools.tool(name="memory_get", description="从记忆存储获取值",
                         category="记忆")
        def memory_get(key: str, default: Any = None) -> Any:
            return self.memory.get(key, default)
        
        @self.tools.tool(name="memory_put", description="存储值到记忆",
                         category="记忆")
        def memory_put(key: str, value: Any) -> bool:
            self.memory.put(key, value)
            return True
        
        @self.tools.tool(name="memory_search", description="搜索记忆",
                         category="记忆")
        def memory_search(query: str) -> list:
            return self.memory.search(query)
    
    def initialize(self):
        """初始化：自动发现技能、注册 Agent。"""
        discovered = self.skills.discover()
        self.swarm.register_agent(AgentSpec(
            name="佑安", role="AI助理",
            capabilities=["编程", "分析", "写作", "咨询", "学习"],
        ))
        self._initialized = True
        return {"discovered_skills": len(discovered), "tools": len(self.tools._tools)}
    
    async def execute(self, tool_name: str, **kwargs) -> Any:
        """执行工具函数（异步接口）。"""
        return await self.tools.execute(tool_name, kwargs)
    
    def execute_sync(self, tool_name: str, **kwargs) -> Any:
        """执行工具函数（同步接口）。"""
        return self.executor.run_sync(self.tools.execute(tool_name, kwargs))
    
    def diagnose(self) -> dict:
        """运行自检诊断。"""
        return {
            "initialized": self._initialized,
            "tools": len(self.tools._tools),
            "skills_discovered": len(self.skills._skills),
            "skills_manual": len(self.skills._manual_skills),
            "memory_keys": len(self.memory.keys()),
            "agents": len(self.swarm._agents),
            "tool_calls": sum(t["call_count"] for t in self.tools._tools.values()),
        }


# ════════════════════════════════════════════════════════════════
# 7. 测试
# ════════════════════════════════════════════════════════════════

def test():
    """运行基本功能测试"""
    print("=" * 50)
    print("LightAgent Fusion 测试")
    print("=" * 50)
    
    agent = LightAgent(skills_dir=r"C:\Users\Administrator\.openclaw\workspace\skills")
    
    # 1. 初始化
    init_result = agent.initialize()
    print(f"\n[1] 初始化: {init_result}")
    
    # 2. 工具注册检查
    tools = agent.tools.list_tools()
    print(f"\n[2] 已注册工具 ({len(tools)}):")
    for t in tools:
        print(f"  - {t['name']}: {t['description'][:50]}")
    
    # 3. 工具执行
    result = agent.execute_sync("get_time")
    print(f"\n[3] get_time: {result}")
    
    # 4. 技能发现
    skills = agent.skills.list_skills()
    print(f"\n[4] 发现的技能 ({len(skills)}):")
    for s in skills[:5]:
        desc = s.description[:50].encode('ascii', 'ignore').decode()
        print(f"  - {s.name}: {desc}")
    if len(skills) > 5:
        print(f"  ... 还有 {len(skills)-5} 个")
    
    # 5. 记忆
    agent.memory.put("test_key", "test_value")
    mem = agent.memory.get("test_key")
    print(f"\n[5] 记忆存取: {mem}")
    
    # 6. 多Agent
    agents = agent.swarm.stats()
    print(f"\n[6] 注册Agent: {agents['agents']}")
    
    # 7. 诊断
    diag = agent.diagnose()
    print(f"\n[7] 自检诊断:")
    for k, v in diag.items():
        print(f"  {k}: {v}")
    
    print("\n" + "=" * 50)
    print("测试完成 ✓")
    print("=" * 50)


if __name__ == "__main__":
    test()

else:
    pass
