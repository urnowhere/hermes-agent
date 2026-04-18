import { useState, useEffect } from 'react';

export type ToastType = 'info' | 'success' | 'error' | 'warning';

export interface Toast {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  duration?: number;
}

type ToastListener = (toasts: Toast[]) => void;

let toasts: Toast[] = [];
const listeners: Set<ToastListener> = new Set();
let nextId = 0;

function notify() {
  for (const listener of listeners) {
    listener(toasts);
  }
}

export const toastStore = {
  add(type: ToastType, title: string, message?: string, duration: number = 5000) {
    const id = `toast-${nextId++}`;
    const toast: Toast = { id, type, title, message, duration };
    toasts = [...toasts, toast];
    notify();

    if (duration > 0) {
      setTimeout(() => {
        this.remove(id);
      }, duration);
    }
    return id;
  },
  
  remove(id: string) {
    toasts = toasts.filter(t => t.id !== id);
    notify();
  },

  success(title: string, message?: string, duration?: number) { return this.add('success', title, message, duration); },
  error(title: string, message?: string, duration?: number) { return this.add('error', title, message, duration); },
  info(title: string, message?: string, duration?: number) { return this.add('info', title, message, duration); },
  warning(title: string, message?: string, duration?: number) { return this.add('warning', title, message, duration); },

  subscribe(listener: ToastListener) {
    listeners.add(listener);
    listener(toasts);
    return () => listeners.delete(listener);
  }
};

export function useToasts() {
  const [currentToasts, setCurrentToasts] = useState<Toast[]>(toasts);

  useEffect(() => {
    const unsubscribe = toastStore.subscribe(setCurrentToasts);
    return () => { unsubscribe(); };
  }, []);

  return currentToasts;
}
