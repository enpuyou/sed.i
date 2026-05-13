"use client";

import { useState, useCallback } from "react";

export function useConfirmAction(onConfirm: () => void) {
  const [armed, setArmed] = useState(false);

  const arm = useCallback(() => setArmed(true), []);
  const cancel = useCallback(() => setArmed(false), []);
  const trigger = useCallback(() => {
    setArmed(false);
    onConfirm();
  }, [onConfirm]);
  // First call arms, second call triggers — matches "click once to arm, again to confirm"
  const toggle = useCallback(() => {
    if (armed) {
      trigger();
    } else {
      arm();
    }
  }, [armed, trigger, arm]);

  return { armed, arm, cancel, trigger, toggle };
}
