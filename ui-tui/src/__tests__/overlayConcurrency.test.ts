import { beforeEach, describe, expect, it } from 'vitest'

import { $isBlocked, $overlayRevision, patchOverlayState, resetOverlayState } from '../app/overlayStore.js'

beforeEach(() => {
  resetOverlayState()
})

describe('overlay store coordination', () => {
  it('bumps revision on each patch and on reset', () => {
    const r0 = $overlayRevision.get()

    patchOverlayState({ picker: true })
    expect($overlayRevision.get()).toBe(r0 + 1)

    patchOverlayState({ picker: false })
    expect($overlayRevision.get()).toBe(r0 + 2)

    resetOverlayState()
    expect($overlayRevision.get()).toBe(r0 + 3)
  })

  it('marks blocked when an interactive overlay is present', () => {
    expect($isBlocked.get()).toBe(false)

    patchOverlayState({
      confirm: {
        onConfirm: () => {},
        title: 'Proceed?'
      }
    })

    expect($isBlocked.get()).toBe(true)

    patchOverlayState({ confirm: null })

    expect($isBlocked.get()).toBe(false)
  })
})
