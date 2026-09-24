/**
 * 统一错误提示（FE-02）。
 *
 * 骨架期不引入 UI 组件库：提示落到页面右上角的容器里。
 * 无 DOM 的环境（SSR / vitest 的 node 环境）退化为 console —— 提示失败
 * 绝不能反过来把请求链路搞崩。
 */

const TOAST_ID = 'aids-toast'

function container(): HTMLElement | null {
  if (typeof document === 'undefined') {
    return null
  }
  let node = document.getElementById(TOAST_ID)
  if (!node) {
    node = document.createElement('div')
    node.id = TOAST_ID
    node.setAttribute('role', 'alert')
    Object.assign(node.style, {
      position: 'fixed',
      top: '16px',
      right: '16px',
      zIndex: '9999',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
    })
    document.body.appendChild(node)
  }
  return node
}

export function notifyError(message: string): void {
  const host = container()
  if (!host) {
    console.error('[AIDS]', message)
    return
  }
  const item = document.createElement('div')
  item.textContent = message
  Object.assign(item.style, {
    padding: '10px 14px',
    borderRadius: '6px',
    background: '#fff2f0',
    border: '1px solid #ffccc7',
    color: '#a8071a',
    fontSize: '14px',
    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.12)',
  })
  host.appendChild(item)
  window.setTimeout(() => item.remove(), 4000)
}
