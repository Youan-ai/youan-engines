#!/usr/bin/env python3
"""
应急纠错机制 — ECC (Emergency Correction Circuit)
===================================================
双保险系统：
1. 网络离线自动切换 (WiFi备用/离线模式)
2. 程序出错自动回退 (Checkpoint + Rollback)

钟明 2026-04-29 指令:"一旦网络不可用立即启动备用WiFi或程序出错马上回退纠正"
"""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


# ════════════════════════════════════════════════════════════════
# 1. 网络监控器
# ════════════════════════════════════════════════════════════════

class NetworkMonitor:
    """监控网络状态，离线时自动切换策略。"""
    
    def __init__(self, primary_test: List[str] = None,
                 backup_test: List[str] = None):
        self.primary_test = primary_test or [
            "api.github.com", "google.com", "bing.com"
        ]
        self.backup_test = backup_test or [
            "192.168.1.1", "10.0.0.1"  # 局域网网关
        ]
        self._online = None
        self._last_check = 0
        self._check_interval = 30  # 30秒检查一次
        self._fail_count = 0
        self._fail_threshold = 3  # 连续3次失败视为离线
        self._history: List[dict] = []
        self._backup_wifi_ssid = None
        self._backup_wifi_password = None
    
    def check(self) -> bool:
        """检查网络连通性。连续失败3次才判定离线。"""
        now = time.time()
        if now - self._last_check < self._check_interval and self._online is not None:
            return self._online
        
        self._last_check = now
        
        # Windows下用 powershell 测试网络
        targets = self.primary_test if self._fail_count < self._fail_threshold else self.backup_test
        
        for target in targets[:3]:  # 最多测3个
            try:
                # Test-NetConnection 更快更准
                cmd = f"powershell -Command \"Test-NetConnection {target} -Port 443 -WarningAction SilentlyContinue | Select-Object -ExpandProperty TcpTestSucceeded\""
                result = subprocess.run(
                    ["powershell", "-Command", f"Test-NetConnection {target} -Port 443 -WarningAction SilentlyContinue | Select-Object -ExpandProperty TcpTestSucceeded"],
                    capture_output=True, text=True, timeout=5
                )
                if "True" in result.stdout:
                    self._online = True
                    self._fail_count = 0
                    self._log("online", f"Connected to {target}")
                    return True
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
        
        self._fail_count += 1
        is_offline = self._fail_count >= self._fail_threshold
        
        if is_offline:
            self._online = False
            self._log("offline", f"Network unavailable ({self._fail_count} failures)")
            self._trigger_backup()
        else:
            self._online = True  # 暂时还标记为在线
            self._log("warning", f"Transient failure ({self._fail_count}/{self._fail_threshold})")
        
        return self._online
    
    def register_backup_wifi(self, ssid: str, password: str = ""):
        """注册备用WiFi。"""
        self._backup_wifi_ssid = ssid
        self._backup_wifi_password = password
    
    def _trigger_backup(self):
        """切换备用WiFi。"""
        if not self._backup_wifi_ssid:
            self._log("backup", "No backup WiFi configured, entering offline mode")
            return
        
        self._log("backup", f"Switching to backup WiFi: {self._backup_wifi_ssid}")
        try:
            # Windows添加WiFi配置
            profile = f'''<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{self._backup_wifi_ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{self._backup_wifi_ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{self._backup_wifi_password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>'''
            with open("_backup_wifi_profile.xml", "w") as f:
                f.write(profile)
            subprocess.run(
                ["netsh", "wlan", "add", "profile", "filename=_backup_wifi_profile.xml"],
                capture_output=True, timeout=10
            )
            subprocess.run(
                ["netsh", "wlan", "connect", "name=" + self._backup_wifi_ssid],
                capture_output=True, timeout=10
            )
            os.remove("_backup_wifi_profile.xml")
        except Exception as e:
            self._log("backup_error", f"WiFi switch failed: {e}")
    
    def _log(self, event: str, detail: str):
        entry = {"time": datetime.now().isoformat(), "event": event, "detail": detail}
        self._history.append(entry)
        print(f"[ECC-NET] {event}: {detail}")
    
    def status(self) -> dict:
        return {
            "online": self._online,
            "fail_count": self._fail_count,
            "last_check": datetime.fromtimestamp(self._last_check).isoformat() if self._last_check else None,
            "history": self._history[-5:],
        }


# ════════════════════════════════════════════════════════════════
# 2. 检查点系统
# ════════════════════════════════════════════════════════════════

class Checkpoint:
    """单个检查点，记录可回退的状态。"""
    def __init__(self, name: str, data: dict, parent: str = None):
        self.name = name
        self.data = data
        self.parent = parent
        self.time = time.time()
        self.id = f"cp_{int(self.time)}_{hash(name) % 10000}"
    
    def restore(self) -> dict:
        """恢复到此检查点的数据。"""
        return dict(self.data)


class CheckpointManager:
    """检查点管理器。支持多层回退。
    
    用法:
        cpm = CheckpointManager()
        cpm.save("task1", {"progress": 50, "file": "result.txt"})
        ...做了一些操作...
        if something_wrong:
            data = cpm.rollback()  # 回退到最近检查点
    """
    
    def __init__(self, max_depth: int = 10):
        self._checkpoints: List[Checkpoint] = []
        self._max_depth = max_depth
        self._history: List[dict] = []
    
    def save(self, name: str, data: dict) -> str:
        """保存检查点。超过最大深度则丢弃最旧的。"""
        parent = self._checkpoints[-1].id if self._checkpoints else None
        cp = Checkpoint(name, dict(data), parent)
        self._checkpoints.append(cp)
        if len(self._checkpoints) > self._max_depth:
            self._checkpoints.pop(0)
        self._log("save", f"Checkpoint '{name}' saved (depth={len(self._checkpoints)})")
        return cp.id
    
    def rollback(self, steps: int = 1) -> Optional[dict]:
        """回退 N 步。返回恢复后的数据。"""
        if steps > len(self._checkpoints):
            steps = len(self._checkpoints)
        if steps <= 0:
            return None
        
        target = self._checkpoints[-steps]
        data = target.restore()
        
        # 删除目标之后的所有检查点
        self._checkpoints = self._checkpoints[:len(self._checkpoints) - steps]
        
        self._log("rollback", f"Rolled back {steps} step(s) to '{target.name}'")
        return data
    
    def rollback_to(self, cp_id: str) -> Optional[dict]:
        """回退到指定检查点。"""
        for i, cp in enumerate(self._checkpoints):
            if cp.id == cp_id:
                data = cp.restore()
                self._checkpoints = self._checkpoints[:i+1]
                self._log("rollback_to", f"Rolled back to '{cp.name}' ({cp_id})")
                return data
        self._log("error", f"Checkpoint '{cp_id}' not found")
        return None
    
    def latest(self) -> Optional[Checkpoint]:
        return self._checkpoints[-1] if self._checkpoints else None
    
    def _log(self, event: str, detail: str):
        self._history.append({"time": time.time(), "event": event, "detail": detail})
    
    def status(self) -> dict:
        return {
            "depth": len(self._checkpoints),
            "max_depth": self._max_depth,
            "latest": self._checkpoints[-1].name if self._checkpoints else None,
            "history": self._history[-5:],
        }


# ════════════════════════════════════════════════════════════════
# 3. 安全执行器 (try/except + 自动回退)
# ════════════════════════════════════════════════════════════════

class SafeExecutor:
    """带自动回退的安全执行器。
    
    用法:
        executor = SafeExecutor(checkpoint_mgr)
        
        @executor.safe("task_name")
        def my_task(context):
            # 如果这里抛异常，自动回退到上一个检查点
            ...
            executor.checkpoint("step1", {"status": "done"})
    """
    
    def __init__(self, checkpoint_mgr: CheckpointManager, 
                 max_retries: int = 3,
                 on_error: Callable = None):
        self.cpm = checkpoint_mgr
        self.max_retries = max_retries
        self.on_error = on_error or self._default_error_handler
        self._history: List[dict] = []
    
    def safe(self, name: str):
        """装饰器：自动try/except + 回退。"""
        def decorator(func):
            def wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(1, self.max_retries + 1):
                    try:
                        # 执行前保存检查点
                        self.cpm.save(f"{name}_attempt_{attempt}", {
                            "func": name, "args": str(args)[:100],
                            "attempt": attempt, "time": time.time()
                        })
                        result = func(*args, **kwargs)
                        self._log("success", f"'{name}' completed (attempt {attempt})")
                        return result
                    except Exception as e:
                        last_error = e
                        tb = traceback.format_exc()
                        self._log("error", f"'{name}' failed (attempt {attempt}/{self.max_retries}): {e}")
                        
                        # 回退
                        restored = self.cpm.rollback()
                        if restored:
                            self._log("rollback", f"Restored state: {restored}")
                        
                        # 调用错误处理器
                        self.on_error(name, attempt, e, tb)
                        
                        if attempt < self.max_retries:
                            time.sleep(1 * attempt)  # 递增等待
                
                raise RuntimeError(f"'{name}' failed after {self.max_retries} attempts: {last_error}")
            return wrapper
        return decorator
    
    def checkpoint(self, name: str, data: dict) -> str:
        """显式保存检查点。"""
        return self.cpm.save(name, data)
    
    def _default_error_handler(self, name: str, attempt: int, error: Exception, tb: str):
        """默认错误处理：打印到stderr。"""
        print(f"[ECC] Error in '{name}' (attempt {attempt}): {error}", file=sys.stderr)
    
    def _log(self, event: str, detail: str):
        self._history.append({"time": time.time(), "event": event, "detail": detail})


# ════════════════════════════════════════════════════════════════
# 4. 文件备份器
# ════════════════════════════════════════════════════════════════

class FileBackup:
    """自动备份重要文件，出错时恢复。"""
    
    def __init__(self, backup_dir: str = "_ecc_backups"):
        self.backup_dir = backup_dir
        os.makedirs(backup_dir, exist_ok=True)
        self._manifest: Dict[str, List[str]] = {}  # file -> backup_ids
    
    def backup(self, filepath: str) -> str:
        """备份单个文件到备份目录。"""
        if not os.path.exists(filepath):
            return None
        
        import hashlib
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(filepath, "rb") as f:
            content = f.read()
        backup_name = f"{os.path.basename(filepath)}.{ts}.bak"
        backup_path = os.path.join(self.backup_dir, backup_name)
        
        with open(backup_path, "wb") as f:
            f.write(content)
        
        self._manifest.setdefault(filepath, []).append(backup_path)
        return backup_path
    
    def restore(self, filepath: str, version: int = -1) -> bool:
        """恢复文件到指定版本。version=-1为最新备份。"""
        backups = self._manifest.get(filepath, [])
        if not backups:
            return False
        
        if version < 0:
            backup_path = backups[-1]
        elif version < len(backups):
            backup_path = backups[version]
        else:
            return False
        
        try:
            with open(backup_path, "rb") as f:
                content = f.read()
            with open(filepath, "wb") as f:
                f.write(content)
            return True
        except Exception:
            return False
    
    def list_backups(self, filepath: str = None) -> list:
        if filepath:
            return self._manifest.get(filepath, [])
        return list(self._manifest.keys())


# ════════════════════════════════════════════════════════════════
# 5. 主控 ECC
# ════════════════════════════════════════════════════════════════

class EmergencyCorrectionCircuit:
    """应急纠错总控。整合网络监控+检查点+安全执行+文件备份。"""
    
    def __init__(self):
        self.network = NetworkMonitor()
        self.checkpoints = CheckpointManager(max_depth=20)
        self.executor = SafeExecutor(self.checkpoints)
        self.files = FileBackup()
        self._active = False
        self._events: List[dict] = []
    
    def start(self):
        """启动监控。"""
        self._active = True
        self._log("START", "ECC system activated")
    
    def stop(self):
        self._active = False
        self._log("STOP", "ECC system deactivated")
    
    def auto_backup_workspace(self):
        """自动备份工作空间关键文件。"""
        critical_files = [
            "SOUL.md", "AGENTS.md", "MEMORY.md", 
            "HEARTBEAT.md", "USER.md", "IDENTITY.md"
        ]
        backed_up = []
        for f in critical_files:
            if os.path.exists(f):
                path = self.files.backup(f)
                if path:
                    backed_up.append(f)
        self._log("BACKUP", f"Backed up {len(backed_up)} critical files")
        return backed_up
    
    def health_check(self) -> dict:
        """全面健康检查。"""
        online = self.network.check()
        cp_status = self.checkpoints.status()
        
        return {
            "active": self._active,
            "network": {
                "online": online,
                "fail_count": self.network._fail_count,
            },
            "checkpoints": cp_status,
            "events_count": len(self._events),
            "last_events": self._events[-5:] if self._events else [],
            "timestamp": time.time(),
        }
    
    def _log(self, event: str, detail: str):
        entry = {"time": datetime.now().isoformat(), "event": event, "detail": detail}
        self._events.append(entry)
        if len(self._events) > 100:
            self._events = self._events[-100:]
        print(f"[ECC] {event}: {detail}")


# ════════════════════════════════════════════════════════════════
# 6. 测试
# ════════════════════════════════════════════════════════════════

def test():
    print("=" * 50)
    print("ECC Emergency Correction Circuit Test")
    print("=" * 50)
    
    ecc = EmergencyCorrectionCircuit()
    ecc.start()
    
    # 1. 备份
    backed = ecc.auto_backup_workspace()
    print(f"\n[1] Workspace backup: {len(backed)} files")
    
    # 2. 检查点
    ecc.checkpoints.save("step_1", {"progress": 25, "file": "test.py"})
    ecc.checkpoints.save("step_2", {"progress": 50, "file": "test.py"})
    ecc.checkpoints.save("step_3", {"progress": 75, "file": "test.py"})
    assert len(ecc.checkpoints._checkpoints) == 3
    print(f"[2] Checkpoints: 3 saved")
    
    # 3. 回退
    restored = ecc.checkpoints.rollback(steps=1)
    assert restored["progress"] == 75
    print(f"[3] Rollback 1 step: progress={restored['progress']}")
    
    # 4. 安全执行
    @ecc.executor.safe("test_task")
    def good_task():
        return "success"
    
    result = good_task()
    assert result == "success"
    print(f"[4] Safe exec (success): {result}")
    
    # 5. 错误+自动回退
    attempt_count = [0]
    
    @ecc.executor.safe("fail_then_retry")
    def flaky_task():
        attempt_count[0] += 1
        if attempt_count[0] < 3:
            raise ValueError(f"Simulated failure #{attempt_count[0]}")
        return "recovered"
    
    result = flaky_task()
    assert result == "recovered"
    assert attempt_count[0] == 3
    print(f"[5] Safe exec (fail 2x, recover): attempts={attempt_count[0]}, result='{result}'")
    
    # 6. 网络检查（不阻塞，超时5秒）
    online = ecc.network.check()
    print(f"[6] Network check: {'ONLINE' if online else 'OFFLINE'}")
    
    # 7. 健康报告
    report = ecc.health_check()
    print(f"[7] Health: active={report['active']}, "
          f"net={report['network']['online']}, "
          f"cp={report['checkpoints']['depth']}")
    
    ecc.stop()
    
    print("\n" + "=" * 50)
    print("ALL ECC TESTS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    test()
