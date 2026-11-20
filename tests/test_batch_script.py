# -*- coding: utf-8 -*-
"""分支 D（`脚本/html2md_batch.ps1`）端到端回归。

需要 Windows + pwsh 7 + pandoc；缺任一时整组 skip（不伪装通过）。
运行：python -m unittest discover -s tests -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, '脚本', 'html2md_batch.ps1')
PWSH = shutil.which('pwsh')
HAS_PANDOC = shutil.which('pandoc') is not None
RUNNABLE = bool(PWSH) and HAS_PANDOC and os.name == 'nt'

A_HTML = '<html><body><h1>甲</h1><p>正文一。</p></body></html>'
B_HTML = '<html><body><h2>乙</h2><ul><li>一</li><li>二</li></ul></body></html>'


def run_script(args, cwd):
    return subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', SCRIPT] + args,
                          cwd=cwd, capture_output=True, text=True)


@unittest.skipUnless(RUNNABLE, '需要 Windows + pwsh 7 + pandoc')
class BatchScriptTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='hmt-batch-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        with open(os.path.join(self.tmp, 'a.html'), 'w', encoding='utf-8') as f:
            f.write(A_HTML)
        os.mkdir(os.path.join(self.tmp, 'sub'))
        with open(os.path.join(self.tmp, 'sub', 'b.html'), 'w', encoding='utf-8') as f:
            f.write(B_HTML)

    def test_mirrors_source_tree(self):
        proc = run_script(['-OutDir', 'md_output', '-Quiet'], self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'md_output', 'a.md')))
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, 'md_output', 'sub', 'b.md')),
            '子目录结构必须被镜像')
        with open(os.path.join(self.tmp, 'md_output', 'a.md'), encoding='utf-8') as f:
            self.assertIn('# 甲', f.read())

    def test_incremental_second_run_skips(self):
        run_script(['-OutDir', 'md_output', '-Quiet'], self.tmp)
        proc = run_script(['-OutDir', 'md_output', '-Incremental', '-Quiet'], self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('待转换 0 个', proc.stdout)
        self.assertIn('跳过 2 个', proc.stdout)

    def test_log_written_on_incremental_skip(self):
        """增量复跑会走「全部已是最新」早退分支，此前这条路径不写日志（已修）。"""
        run_script(['-OutDir', 'md_output', '-Quiet', '-Log', 'run.log'], self.tmp)
        run_script(['-OutDir', 'md_output', '-Incremental', '-Quiet',
                    '-Log', 'run.log'], self.tmp)
        log = os.path.join(self.tmp, 'run.log')
        self.assertTrue(os.path.isfile(log), '增量复跑没有写日志')
        with open(log, encoding='utf-8-sig') as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, '每次运行都要留下一条摘要：%r' % lines)
        self.assertIn('待转换=0', lines[-1])

    def test_missing_source_fails_friendly(self):
        """源不存在时应给出可读报错并 exit 1，而不是抛 Get-ChildItem 堆栈。"""
        proc = run_script(['-Source', os.path.join(self.tmp, 'nope')], self.tmp)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('源路径不存在', proc.stdout + proc.stderr)
        self.assertNotIn('Cannot find path', proc.stdout + proc.stderr)

    def test_wildcard_source_succeeds(self):
        proc = run_script(['-Source', '*.html', '-OutDir', 'w1', '-Quiet'], self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'w1', 'a.md')))
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, 'w1', 'sub', 'b.md')),
                         '通配符只应匹配当前目录这一层')


if __name__ == '__main__':
    unittest.main()
