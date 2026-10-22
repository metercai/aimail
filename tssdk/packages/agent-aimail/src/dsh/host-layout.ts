/** dsh 宿主布局数据(宿主概念, 10-11 三来源裁决「宿主自报/内部 → SDK 内部」)。
 * 独立无副作用模块 ⇒ 可单测, 不触发 register-cli 入口的 main() 自执行。
 * P3-C(2026-10-17): preset 由本模块自报(原 CLI 注册表 register.args `--preset mail`,
 * CLI 提取进 op 信封的旧通道已删); op 信封 merge `{...args, ...extra}` ⇒ 本值直达
 * opAssemble 落绑定顶层, dsh check session 维判据只认它(adapters.rs)。 */
export type DshHostLayout = Record<string, unknown> & {
  preset: string
  skill_expose_patch: string
  skill_expose_ids: string
}

export function hostLayoutArgs(): DshHostLayout {
  const dshHome = process.env.DSH_HOME || `${process.env.HOME || '/root'}/.dsh`
  const profile = process.env.DSH_PROFILE || 'web'
  return {
    preset: 'mail',
    skill_expose_patch: `${dshHome}/profiles/${profile}/cordis.patch.yml`,
    skill_expose_ids: 'skill-filesystem tool-skill',
  }
}
