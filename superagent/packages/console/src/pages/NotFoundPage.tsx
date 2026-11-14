import { CircleOff } from 'lucide-react'
import { Card, EmptyState } from '../components'

export function NotFoundPage() {
  return <Card><EmptyState icon={<CircleOff size={28} />} title="This console view doesn’t exist" description="Return to the instance overview to continue." action={<a className="button primary" href="#/overview">Go to overview</a>} /></Card>
}
