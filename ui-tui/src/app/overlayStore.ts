import { atom, computed } from 'nanostores'

import type { OverlayState } from './interfaces.js'

const buildOverlayState = (): OverlayState => ({
  approval: null,
  clarify: null,
  confirm: null,
  modelPicker: false,
  pager: null,
  picker: false,
  secret: null,
  skillsHub: false,
  sudo: null
})

export const $overlayState = atom<OverlayState>(buildOverlayState())

/** Increments on every overlay mutation for tests and optional subscribers (cross-backend render sequencing). */
export const $overlayRevision = atom(0)

export const $isBlocked = computed(
  $overlayState,
  ({ approval, clarify, confirm, modelPicker, pager, picker, secret, skillsHub, sudo }) =>
    Boolean(approval || clarify || confirm || modelPicker || pager || picker || secret || skillsHub || sudo)
)

export const getOverlayState = () => $overlayState.get()

export const patchOverlayState = (next: Partial<OverlayState> | ((state: OverlayState) => OverlayState)) => {
  const prev = $overlayState.get()
  const merged = typeof next === 'function' ? next(prev) : { ...prev, ...next }

  $overlayState.set(merged)
  $overlayRevision.set($overlayRevision.get() + 1)
}

export const resetOverlayState = () => {
  $overlayState.set(buildOverlayState())
  $overlayRevision.set($overlayRevision.get() + 1)
}
