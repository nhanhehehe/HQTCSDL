import type { ReactNode } from 'react'
import { StatusBadge } from './StatusBadge'
import { StateBanner } from './StateBanner'

export function FeaturePlaceholder({
  stage,
  flows,
  scopeSummary,
  plannedApis,
  contractNotes,
  contextSlot,
}: {
  stage: 'G3' | 'G4' | 'G3/G4'
  flows: string
  scopeSummary: string
  plannedApis: ReadonlyArray<string>
  contractNotes?: ReadonlyArray<string>
  contextSlot?: ReactNode
}) {
  return (
    <section className="card placeholder-card" aria-label={`Khung chờ ${flows}`}>
      <div className="row-between">
        <div className="row">
          <StatusBadge tone="neutral" label={`Giai đoạn ${stage} · Chưa triển khai`} />
          <span className="muted mono">{flows}</span>
        </div>
      </div>

      <StateBanner
        tone="info"
        message="Màn hình khung chờ trung thực cho giai đoạn tiếp theo. Hiện chưa kết nối Backend G3/G4 và tuyệt đối không sử dụng dữ liệu giả hoặc số đếm 0 giả."
      />

      {contextSlot}

      <div className="stack-sm">
        <h3>Phạm vi dự kiến</h3>
        <p>{scopeSummary}</p>
      </div>

      <div className="stack-sm">
        <h3>API dự kiến sẽ kết nối</h3>
        <ul className="stack-sm">
          {plannedApis.map(api => (
            <li key={api}>
              <code>{api}</code>
            </li>
          ))}
        </ul>
      </div>

      {contractNotes && contractNotes.length > 0 && (
        <div className="stack-sm">
          <h3>Ràng buộc & Điểm kiểm soát hợp đồng</h3>
          <ul className="stack-sm">
            {contractNotes.map(note => (
              <li key={note}>
                <small>{note}</small>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
