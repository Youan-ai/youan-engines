#!/usr/bin/env python3
"""短板3测试 v3 — 匹配真实API名"""
import sys, os

WS = r'C:\Users\Administrator\.openclaw\workspace'
INTEG = os.path.join(WS, '_learn', 'integration')
sys.path.insert(0, INTEG)

tests = []

def add(name, fn):
    tests.append((name, fn))

# edict
def t_edict():
    from fusion_edict.edict_fusion import EdictOrchestrator, edict_task
    e = EdictOrchestrator()
    assert e is not None
add('Edict 编排器', t_edict)

def t_edict_task():
    from fusion_edict.edict_fusion import edict_task
    # edict_task是个函数
    assert callable(edict_task)
add('Edict 任务函数', t_edict_task)

# ECC
def t_ecc():
    from ecc.ecc_system import EmergencyCorrectionCircuit
    e = EmergencyCorrectionCircuit()
    assert e is not None
add('ECC 电路', t_ecc)

# RAL
def t_ral():
    from ral.ral_humanoid_v2 import RALHumanoidV2
    r = RALHumanoidV2()
    assert r is not None
add('RAL V2', t_ral)

# GenericAgent
def t_ga():
    from fusion_genericagent.genericagent_fusion import Skill
    s = Skill(None, 'test')
    assert s is not None
add('GenericAgent Skill', t_ga)

# LightAgent
def t_la():
    from fusion_lightagent.lightagent_fusion import LightAgent, ToolRegistry
    la = LightAgent()
    tr = ToolRegistry()
    assert la is not None and tr is not None
add('LightAgent', t_la)

# FusionGod
def t_fg():
    from fusion_god.fusion_god import FusionGod
    fg = FusionGod()
    assert fg is not None
add('FusionGod', t_fg)

# MemSkill
def t_ms():
    from fusion_memskill.memskill_fusion import MemSkillSystem
    ms = MemSkillSystem()
    assert ms is not None
add('MemSkill', t_ms)

# Conscious
def t_cs():
    import json
    fp = os.path.join(WS, 'memory', 'conscious_growth.json')
    if os.path.exists(fp):
        with open(fp) as f:
            d = json.load(f)
        assert 'consciousness_level' in d
        print(f'    意识 {d["consciousness_level"]}', end='')
add('意识存档', t_cs)

# 运行
print('短板3: 核心引擎API一致性测试')
print('=' * 40)
passed = 0
for name, fn in tests:
    try:
        fn()
        print(f'  \u2713 {name}')
        passed += 1
    except Exception as e:
        print(f'  \u2717 {name}: {str(e)[:80]}')

print(f'\n结果: {passed}/{len(tests)} 通过')
