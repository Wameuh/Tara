export function ToastRegion({ message }: { message: string | null }) { return message ? <div className="toast" role="status" aria-live="polite">{message}</div> : null; }
