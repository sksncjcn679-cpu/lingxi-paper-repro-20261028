#!/usr/bin/env python3
"""Lingxi Code: a local, dependency-free, interactive frontend demonstration."""
import argparse
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys
import time

VERSION='0.1.0'
CORAL='\033[38;2;214;137;109m';MUTED='\033[38;2;133;135;140m';INK='\033[38;2;221;220;216m';GREEN='\033[38;2;132;173;144m';RESET='\033[0m';BOLD='\033[1m'
ROLES=[('Planner','任务规划'),('Retriever','知识检索'),('Executor','工具执行'),('Summarizer','结果汇总'),('Evaluator','评测记录')]
MEMORIES=[{'id':'mem_550e8400','title':'内核软件包依赖冲突','source':'Executor','score':.94,'tags':['openEuler','依赖']},{'id':'mem_7f3a20cd','title':'dnf 依赖树与解决路径','source':'Retriever','score':.89,'tags':['dnf','依赖']},{'id':'mem_1b2c9a10','title':'kmod 模块兼容性检查','source':'Summarizer','score':.86,'tags':['kernel','回滚']},{'id':'mem_4ea190fe','title':'OOM 日志事件关联','source':'Executor','score':.92,'tags':['日志','OOM']}]

def enable_ansi():
    if os.name=='nt':
        try:
            k=ctypes.windll.kernel32;h=k.GetStdHandle(-11);mode=ctypes.c_uint();k.GetConsoleMode(h,ctypes.byref(mode));k.SetConsoleMode(h,mode.value|4)
        except Exception:pass
    try:sys.stdout.reconfigure(encoding='utf-8');sys.stdin.reconfigure(encoding='utf-8')
    except (AttributeError,ValueError):pass

def out(s='',color=INK,end='\n'):
    print(color+s+RESET,end=end,flush=True)

class App:
    def __init__(self,args):
        self.args=args;self.mode='structured';self.logs=[];self.task='';self.complete=False
        self.outdir=Path(args.output_dir) if args.output_dir else Path(__file__).resolve().parent/'demo_records'
    def banner(self):
        w=min(shutil.get_terminal_size((92,30)).columns-2,96)
        out('');out('  ╭'+ '─'*(w-4)+'╮',MUTED)
        out('  │  '+BOLD+'灵犀栈 Code'+RESET+MUTED+'  '+VERSION+'  ·  本地工作区',MUTED)
        out('  │',MUTED)
        out('  │      .--.       多智能体协作，从一个任务开始。',INK)
        out('  │     |o_o |      SACP · SVST · DSMM',INK)
        out('  │     |:_/ |',INK);out('  │    //   \\ \\',INK);out("  │   (|     | )",INK);out("  │   /'\\_   _/`\\",CORAL);out("  │   \\___)=(___/",CORAL)
        out('  │',MUTED)
        out('  │   前端原型 · 模拟数据 · 不连接真实后端',MUTED)
        out('  ╰'+ '─'*(w-4)+'╯',MUTED)
        out('');out('  /help 查看命令   /agents 查看角色   /memory 检索记忆',MUTED)
        out('  输入任务描述，或试试「分析 openEuler 软件包依赖链」。',MUTED);out('')
    def log(self,role,title):
        self.logs.append({'time':dt.datetime.now().isoformat(timespec='seconds'),'role':role,'message':title,'demo':True})
    def agents(self):
        out('  ● Agent pipeline',CORAL)
        for i,(n,c) in enumerate(ROLES):out(f'    {i+1:02d}  {n:<13} {c}')
        out('    └─ DSMM：候选检索 → 结果复用 → 经验写回',MUTED)
    def memory(self,key=''):
        matches=[m for m in MEMORIES if key.lower() in (' '.join([m['title'],m['source'],*m['tags']])).lower()]
        out(f'  ◇ 共享记忆 · {len(matches)} 条示例候选',CORAL)
        for m in matches:
            out(f"    {m['title']}  {GREEN}{m['score']:.2f}{INK}")
            out(f"      {m['id']} · {m['source']} · {' / '.join(m['tags'])}",MUTED)
        if not matches:out('    没有匹配的示例，试试「依赖」或「日志」。',MUTED)
        self.log('DSMM','检索关键词：'+(key or '全部'))
    def run(self,task):
        self.task=task;self.complete=False;self.log('Runtime','提交任务：'+task)
        out('');out('  ✻ 组织五类角色协作…',CORAL);out('  当前执行是浏览器/终端演示，不运行真实工具。',MUTED)
        details=['拆解需求，形成任务图','关联 2 条示例记忆' if self.mode=='structured' else '使用文本传递示例','展示预设结构化输出','整理结果摘要与来源','记录演示阶段与指标']
        for (n,c),detail in zip(ROLES,details):
            if not self.args.no_animation:
                for tick in range(6):
                    print('\r'+CORAL+'  '+['✻','✽','✶','✢'][tick%4]+' '+INK+f'{n:<13} '+MUTED+detail+RESET+' '*8,end='',flush=True)
                    time.sleep(.13)
                print('\r'+' '*100+'\r',end='')
            out(f'  {GREEN}✓{INK} {n:<13} {detail}');self.log(n,detail)
        self.complete=True
        out('');out('  ● 演示流程完成',CORAL)
        out('    1. 规划阶段组织任务拆解与阶段依赖。')
        out('    2. 检索阶段关联示例经验，保留主题和来源。')
        out('    3. 执行、汇总与评测阶段形成预设结果与演示记录。')
        out('');out('  /memory 查看记忆   /report 导出记录   /replay 重放',MUTED)
    def report(self):
        self.outdir.mkdir(parents=True,exist_ok=True)
        p=self.outdir/('lingxi_demo_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
        p.write_text(json.dumps({'project':'灵犀栈','demo':True,'notice':'前端原型模拟记录，非真实后端任务或实验结果','mode':self.mode,'task':self.task,'completed':self.complete,'events':self.logs},ensure_ascii=False,indent=2),encoding='utf-8')
        out('  ✓ 示例记录已导出',GREEN);out('    '+str(p),MUTED);out('    demo: true · 不含真实实验测量',MUTED)
        return p
    def help(self):
        out('  斜杠命令',CORAL)
        for cmd,desc in [('/help','查看命令'),('/agents','五类角色与分工'),('/memory [关键词]','检索示例记忆'),('/mode [baseline|structured]','查看或切换协作模式'),('/status','查看当前任务状态'),('/report','导出带 demo 标识的 JSON'),('/replay','重放上一条任务'),('/clear','清屏'),('/new','开启新任务'),('/exit','退出')]:
            out('    '+cmd.ljust(30)+desc)
        out('  所有命令仅操作本地演示状态，不执行系统命令。',MUTED)
    def command(self,line):
        line=line.strip()
        if not line:return True
        if not line.startswith('/'):
            self.run(line);return True
        cmd,_,arg=line.partition(' ')
        if cmd in ['/exit','/quit']:return False
        if cmd=='/help':self.help()
        elif cmd=='/agents':self.agents()
        elif cmd=='/memory':self.memory(arg.strip())
        elif cmd=='/mode':
            if not arg:out('  当前模式：'+self.mode,CORAL)
            elif arg.strip() in ['baseline','structured']:self.mode=arg.strip();out('  ✓ 已切换：'+self.mode,GREEN)
            else:out('  可用模式：baseline / structured',MUTED)
        elif cmd=='/report':self.report()
        elif cmd=='/status':out('  模式：'+self.mode+'  ·  状态：'+('已完成' if self.complete else '待命'),CORAL);out('  任务：'+(self.task or '暂无'),MUTED)
        elif cmd=='/replay':self.run(self.task or '分析 openEuler 软件包依赖链，并给出可复现的修复方案')
        elif cmd=='/new':self.task='';self.complete=False;out('  ✓ 已开启新任务',GREEN)
        elif cmd=='/clear':out('\033[2J\033[H',end='');self.banner()
        else:out('  未知命令，输入 /help 查看帮助。',MUTED)
        return True
    def loop(self):
        while True:
            try:
                out('');out('  '+ '─'*min(shutil.get_terminal_size((90,30)).columns-4,88),MUTED)
                line=input(CORAL+'  ❯ '+RESET)
                out('  '+ '─'*min(shutil.get_terminal_size((90,30)).columns-4,88),MUTED)
                if not self.command(line):out('  再见。',MUTED);break
            except KeyboardInterrupt:
                out('\n  演示已中断，输入 /exit 退出。',MUTED)
            except EOFError:break

def main():
    parser=argparse.ArgumentParser(description='灵犀栈 Code · 本地命令行原型')
    parser.add_argument('--demo',action='store_true',help='运行预设演示后退出')
    parser.add_argument('--no-animation',action='store_true',help='关闭逐阶段动画')
    parser.add_argument('--output-dir',help='示例 JSON 导出目录')
    args=parser.parse_args();enable_ansi();app=App(args);app.banner()
    if args.demo:
        app.command('分析 openEuler 软件包依赖链，并给出可复现的修复方案');app.command('/memory 依赖');app.command('/report')
    else:app.loop()

if __name__=='__main__':main()
