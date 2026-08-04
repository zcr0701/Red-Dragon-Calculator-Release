# 对比测试：C++ 原型 vs Python 原版（beam 模式输出应逐字一致）
param(
    [string]$PyDir = "$env:TEMP\rd_cpp_test\代码"
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$cpp = Join-Path $PSScriptRoot 'build\red_dragon_calc.exe'
$py = Join-Path $PyDir 'red_dragon_calculator.py'

if (-not (Test-Path $cpp)) { throw "未找到 $cpp，请先运行 build.bat" }
if (-not (Test-Path $py)) {
    # 首次：把计算器与归档模块复制到临时目录，避免 Python 运行时改动仓库存档
    $src = 'C:\Users\22501\Documents\炉石传说\代码'
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $src 'red_dragon_calculator.py') -Destination $PyDir
    Copy-Item -LiteralPath (Join-Path $src 'archive.py') -Destination $PyDir
}

$cases = @(
    @{ name = 'A_基础硬币'; args = @('--hand','鲨鱼之灵,幸运币,幸运币,幸运币,生命的缚誓者阿莱克丝塔萨','--mana-crystals','10','--mana','10','--search','--beam','--beam-width','300','--max-depth','12','--show-limit','20') },
    @{ name = 'B_刀油狐人'; args = @('--hand','鲨鱼之灵,狐人老千,斯卡布斯·刀油,幸运币,幸运币,幸运币,生命的缚誓者阿莱克丝塔萨','--mana-crystals','10','--mana','10','--search','--beam','--beam-width','400','--max-depth','15','--show-limit','20') },
    @{ name = 'C_暗影步回手'; args = @('--hand','鲨鱼之灵,幸运币,幸运币,暗影步,生命的缚誓者阿莱克丝塔萨,生命的缚誓者阿莱克丝塔萨','--mana-crystals','10','--mana','10','--search','--beam','--beam-width','400','--max-depth','15','--show-limit','20') },
    @{ name = 'D_牛头人发现'; args = @('--hand','乐队经理精英牛头人酋长,幸运币,幸运币,幸运币,幸运币,鲨鱼之灵,生命的缚誓者阿莱克丝塔萨','--mana-crystals','10','--mana','10','--search','--beam','--beam-width','400','--max-depth','15','--show-limit','20') },
    @{ name = 'E_殒命暗影'; args = @('--hand','殒命暗影,幸运币,幸运币,幸运币,鲨鱼之灵,暗影步,生命的缚誓者阿莱克丝塔萨','--mana-crystals','10','--mana','10','--search','--beam','--beam-width','400','--max-depth','15','--show-limit','20') },
    @{ name = 'F_已知牌库'; args = @('--hand','行骗,幸运币,幸运币,鲨鱼之灵,生命的缚誓者阿莱克丝塔萨','--deck','狐人老千,暗影步,垂钓时光,鲨鱼之灵','--mana-crystals','10','--mana','10','--search','--beam','--beam-width','400','--max-depth','15','--show-limit','20') }
)

function Normalize([string]$text) {
    # Python 会额外打印“命中公式表/命中存档”缓存提示；只对比“共计算出”之后的
    # 算法输出段，并去掉耗时行。
    $lines = $text -split "`r?`n"
    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^共计算出') { $start = $i; break }
    }
    if ($start -lt 0) { $start = 0 }
    return ($lines[$start..($lines.Count - 1)] |
        Where-Object { $_ -and $_ -notmatch '计算总耗时' -and $_ -notmatch '已重新计算完毕' }) -join "`n"
}

$pass = 0
$fail = 0
foreach ($case in $cases) {
    $caseArgs = $case.args
    $pyOut = (& python $py @caseArgs 2>&1 | Out-String)
    $cppOut = (& $cpp @caseArgs 2>&1 | Out-String)
    $a = Normalize $pyOut
    $b = Normalize $cppOut
    if ($a -eq $b) {
        Write-Host "[PASS] $($case.name)" -ForegroundColor Green
        $pass++
    } else {
        Write-Host "[FAIL] $($case.name)" -ForegroundColor Red
        Write-Host '--- Python ---'
        Write-Host $a
        Write-Host '--- C++ ---'
        Write-Host $b
        $fail++
    }
}
Write-Host "结果：通过 $pass / $($cases.Count)，失败 $fail"
if ($fail -gt 0) { exit 1 } else { exit 0 }
