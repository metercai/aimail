/** dsh host layout data (host concept, 10-11 three-source ruling "host self-report/internal → SDK internal").
 * Independent side-effect-free module ⇒ unit-testable, does not trigger register-cli entry main() self-execution.
 * P3-C (2026-10-17): preset is self-reported by this module (original CLI registry register.args `--preset mail`,
 * CLI extraction into op envelope old channel removed); op envelope merge `{...args, ...extra}` ⇒ this value direct
 * opAssemble binding top-level directly, dsh check session dimension criteria only recognizes it (adapters.rs). */
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
