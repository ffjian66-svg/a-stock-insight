import { useEffect, useState } from 'react'

export type ToastTone = 'success' | 'error' | 'info'
export type ToastItem = { id: number; message: string; tone: ToastTone }

let toastId = 1
let toastItems: ToastItem[] = []
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((listener) => listener())
}

export function toast(message: string, tone: ToastTone = 'success') {
  const id = toastId++
  toastItems = [...toastItems, { id, message, tone }]
  emit()
  window.setTimeout(() => {
    toastItems = toastItems.filter((item) => item.id !== id)
    emit()
  }, 3200)
}

export function useToasts() {
  const [items, setItems] = useState<ToastItem[]>(toastItems)
  useEffect(() => {
    const listener = () => setItems([...toastItems])
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }, [])
  return items
}
