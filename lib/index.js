/**
 * dsha-autojs-bridge —— DSHA bundle 插件
 *
 * 载入时把随包的两样东西安装到运行环境（幂等、不联网、失败只告警不抛错）：
 *   tools/node-proxy.py, tools/ajrpc.py  →  $TOOLS_DIR（默认 /root）
 *   skills/<name>/SKILL.md               →  $DSH_HOME/skills/<name>/（DSH 默认扫描的技能根）
 *
 * 也可不用 bundle 安装：直接跑仓库根的 ./install.sh（等价逻辑）。
 */
import { chmod, copyFile, mkdir, readdir, stat } from 'node:fs/promises'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

export const name = 'dsha-autojs-bridge'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = join(here, '..')

export function apply(ctx, config = {}) {
  const log = (msg) => {
    try { ctx?.logger?.info?.(msg) } catch { /* ignore */ }
    if (config.quiet !== true) console.log(`[dsha-autojs-bridge] ${msg}`)
  }

  ctx.effect(async () => {
    const toolsDir = config.toolsDir || '/root'
    const dshHome = config.dshHome || process.env.DSH_HOME || join(homedir(), '.dsh')
    const skillsDir = join(dshHome, 'skills')
    const installed = []

    // 1) 容器侧工具
    try {
      await mkdir(toolsDir, { recursive: true })
      for (const file of await readdir(join(repoRoot, 'tools'))) {
        if (!file.endsWith('.py')) continue
        const dest = join(toolsDir, file)
        await copyFile(join(repoRoot, 'tools', file), dest)
        await chmod(dest, 0o755)
        installed.push(dest)
      }
    } catch (error) {
      log(`工具安装失败（不影响会话）：${error?.message ?? error}`)
    }

    // 2) 技能（DSH 默认扫描 $DSH_HOME/skills）
    try {
      await mkdir(skillsDir, { recursive: true })
      for (const entry of await readdir(join(repoRoot, 'skills'))) {
        const src = join(repoRoot, 'skills', entry)
        if (!(await stat(src)).isDirectory()) continue
        await mkdir(join(skillsDir, entry), { recursive: true })
        for (const file of await readdir(src)) {
          await copyFile(join(src, file), join(skillsDir, entry, file))
        }
        installed.push(join(skillsDir, entry))
      }
    } catch (error) {
      log(`技能安装失败（不影响会话）：${error?.message ?? error}`)
    }

    log(installed.length
      ? `已就位 ${installed.length} 项：${installed.join(', ')}`
      : '未安装任何文件（检查仓库目录结构）')
    log('手机侧还需一次性设置：AutoJsPro 开「无障碍服务」与「允许远程调试」，首次运行 ajrpc.py auth 时点「永久允许」')
  })
}
