import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Alerts from '../pages/Alerts'
import InvestigationWorkspace from '../pages/InvestigationWorkspace'
import NetworkExplorer from '../pages/NetworkExplorer'
import Transactions from '../pages/Transactions'
import { DATASET, mockApi, renderAt } from './helpers'

vi.mock('../components/CytoGraph', () => ({
  CytoGraph: ({ nodes, edges }: { nodes: unknown[]; edges: unknown[] }) => <div data-testid="graph">{nodes.length} nodes / {edges.length} edges</div>,
  edgeKey: (a: string, b: string) => `${a}>${b}`,
}))
vi.mock('../components/EChart', () => ({ EChart: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} />, axis: () => ({}), PALETTE: [] }))

const ALERT = {
  alert_id: 'AL-1', dataset_id: 'DS-test', entity_id: 'AC000001', entity_type: 'account', alert_type: 'cycle', risk_score: 0.91, risk_band: 'Critical', n_transactions: 3, n_accounts: 3,
  gross_value: 300000, exposed_value: 100000, alert_timestamp: '2026-01-20T10:00:00', investigation_status: 'Not opened', evidence_summary: '3-hop circular flow A → B → C → A', methods: ['cycle'],
  model_version: 'D-lgbm', feature_version: 'fv1', assigned_analyst: null,
}
const ALERT_DETAIL = {
  ...ALERT, investigations: [], disclaimer: 'Potentially suspicious activity flagged for review. This is a model-generated risk estimate, not a finding of wrongdoing.',
  transactions: [{ transaction_id: 'TX1', source_account_id: 'A', destination_account_id: 'B', transaction_timestamp: '2026-01-20T08:00:00', amount: 100000, transaction_type: 'P2P_TRANSFER', model_risk: 0.8 }],
  detail: {
    evidence_summary: ALERT.evidence_summary, detection_methods: ['cycle'], detections: [{ method: 'cycle', strength: 0.8, summary: 'cycle summary', evidence: {}, path: ['A', 'B', 'C', 'A'], start: '', end: '' }], paths: [['A', 'B', 'C', 'A']], accounts: ['A', 'B', 'C'],
    evidence_timestamps: { first: '2026-01-20T08:00:00', last: '2026-01-20T10:00:00' }, risk_breakdown: { score: 0.91, signals: { pattern: 0.8 }, contributions: { pattern: 0.52 }, missing_families: ['model'], method: 'noisy-OR over signal families' },
    alternative_scores: { rules_only: 0.5, model_only: null }, exposure: { gross_value: 300000, exposed_value: 100000, incremental_unique_value: 300000, method: 'm', definition: 'Potentially affected transaction value; not a confirmed loss.' },
    explanation: null, explained_transaction: null, recommended_steps: ['Review the full transaction cycle'], limitations: ['Synthetic data'],
  },
}
const base = { 'GET /datasets': { items: [DATASET] }, 'GET /overview': { model: { version: 'D-lgbm' }, freshness: {} } }

beforeEach(() => localStorage.clear())

describe('alert → investigation flow', () => {
  it('opens an alert, shows evidence, and creates an investigation', async () => {
    const calls = mockApi({
      ...base,
      'GET /alerts/AL-1': ALERT_DETAIL,
      'GET /alerts': { items: [ALERT], total: 1, page: 1, page_size: 25, facets: { band: { Critical: 1 }, alert_type: { cycle: 1 } } },
      'POST /investigations': { investigation_id: 'INV-9' },
    })
    renderAt(<Alerts />, '/alerts', '/alerts')
    const row = await screen.findByRole('button', { name: 'AL-1' })
    expect(screen.getByText('Cycle')).toBeInTheDocument()
    await userEvent.click(row)
    expect(await screen.findByText(/3-hop circular flow/)).toBeInTheDocument()
    expect(screen.getByText(/not a finding of wrongdoing/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: /Evidence/ }))
    expect(await screen.findByText('TX1')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Investigation' }))
    await userEvent.click(screen.getByRole('button', { name: 'Create investigation' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/investigations')).toBe(true))
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ alert_id: 'AL-1' })
    expect(await screen.findByText('other page')).toBeInTheDocument() // navigated to the workspace route
  })

  it('applies alert filters through the API', async () => {
    const calls = mockApi({ ...base, 'GET /alerts': { items: [ALERT], total: 1, page: 1, page_size: 25, facets: { band: { Critical: 1 }, alert_type: { cycle: 1 } } } })
    renderAt(<Alerts />, '/alerts', '/alerts')
    await screen.findByRole('button', { name: 'AL-1' })
    await userEvent.selectOptions(screen.getByLabelText(/risk band/i), 'Critical')
    await waitFor(() => expect(calls.some((c) => c.url.includes('band=Critical'))).toBe(true))
  })

  it('shows a meaningful empty state', async () => {
    mockApi({ ...base, 'GET /alerts': { items: [], total: 0, page: 1, page_size: 25, facets: { band: {}, alert_type: {} } } })
    renderAt(<Alerts />, '/alerts', '/alerts')
    expect(await screen.findByText('No alerts match')).toBeInTheDocument()
  })
})

describe('investigation workspace', () => {
  const inv = (status: string, allowed: string[]) => ({
    investigation_id: 'INV-9', dataset_id: 'DS-test', title: 'Cycle – AC000001', assigned_analyst: null, status, priority: 'High', created_at: '2026-01-21T00:00:00', updated_at: '2026-01-21T00:00:00', closed_at: null,
    disposition: null, findings: null, age_days: 0, alert_ids: ['AL-1'], notes: [], transaction_ids: ['TX1'], created_by: 'analyst@local', allowed_transitions: allowed, disposition_notice: 'Analyst workflow outcome; not a legal finding.',
    history: [{ audit_id: 1, actor: 'analyst@local', at: '2026-01-21T00:00:00', action: 'created', field: 'alert', old: null, new: 'AL-1' }],
  })
  it('changes status and surfaces server-side validation errors', async () => {
    let status = 'New'
    const calls = mockApi({
      ...base,
      'GET /investigations/meta': { statuses: ['New', 'In Review', 'Escalated', 'Resolved', 'Closed'], priorities: [], dispositions: ['Benign activity'], transitions: {} },
      'GET /investigations/INV-9': () => inv(status, status === 'New' ? ['In Review'] : ['Escalated', 'Resolved']),
      'PATCH /investigations/INV-9': (_u: URL, init: RequestInit) => {
        const b = JSON.parse(String(init.body))
        if (b.status === 'Resolved') return new Response(JSON.stringify({ detail: 'a disposition is required to move to Resolved' }), { status: 409 })
        status = b.status ?? status
        return inv(status, [])
      },
      'GET /alerts/AL-1': ALERT_DETAIL,
      'GET /graph/subgraph': { center: 'AC000001', nodes: [], edges: [], truncated: false, alerts_in_view: [], query_ms: 1, note: '' },
    })
    renderAt(<InvestigationWorkspace />, '/investigations/INV-9', '/investigations/:id')
    await userEvent.click(await screen.findByRole('button', { name: 'Move to In Review' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ status: 'In Review' }))
    expect(await screen.findByText('Status changed to In Review')).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: 'Move to Resolved' }))
    expect(await screen.findByText(/disposition is required/)).toBeInTheDocument()
    expect(screen.getAllByText(/not a legal finding/).length).toBeGreaterThan(0)
  })
})

describe('network explorer', () => {
  it('loads a bounded subgraph and warns when it is truncated', async () => {
    const calls = mockApi({
      ...base,
      'GET /graph/subgraph': { center: 'AC000001', nodes: [{ id: 'AC000001', hop: 0, account_type: 'individual', max_risk: 0.9, community: 0, alerts: [] }, { id: 'AC000002', hop: 1, account_type: 'business', max_risk: 0, community: 0, alerts: [] }],
        edges: [{ source: 'AC000001', target: 'AC000002', count: 2, total: 5000, txn_ids: ['T1', 'T2'], first: '', last: '', max: 3000 }], truncated: true, alerts_in_view: [], query_ms: 4.2, note: '' },
    })
    renderAt(<NetworkExplorer />, '/network', '/network')
    await screen.findByText(/No datasets|Choose an account/)
    await userEvent.type(screen.getByLabelText('Centre account'), 'AC000001')
    await userEvent.selectOptions(screen.getByLabelText('Hop depth'), '2')
    await userEvent.click(screen.getByRole('button', { name: 'Load network' }))
    expect(await screen.findByTestId('graph')).toHaveTextContent('2 nodes / 1 edges')
    expect(screen.getByText(/truncated to the heaviest connections/)).toBeInTheDocument()
    const q = calls.find((c) => c.url.startsWith('/api/graph/subgraph'))!.url
    expect(q).toContain('account_id=AC000001')
    expect(q).toContain('hops=2')
    expect(screen.getByLabelText('Legend')).toBeInTheDocument()
  })

  it('shows an error when the account is unknown', async () => {
    mockApi({ ...base, 'GET /graph/subgraph': new Response(JSON.stringify({ detail: 'account has no transactions in this dataset' }), { status: 404 }) })
    renderAt(<NetworkExplorer />, '/network', '/network')
    await screen.findByText(/Choose an account/)
    await userEvent.type(screen.getByLabelText('Centre account'), 'NOPE')
    await userEvent.click(screen.getByRole('button', { name: 'Load network' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('no transactions')
  })
})

describe('transaction explorer', () => {
  it('filters by amount and shows scored / not-scored transactions', async () => {
    const calls = mockApi({
      ...base,
      'GET /transactions': { items: [
        { transaction_id: 'TX1', source_account_id: 'AC1', destination_account_id: 'AC2', transaction_timestamp: '2026-01-02T10:00:00', amount: 250000, transaction_type: 'P2P_TRANSFER', model_risk: 0.77 },
        { transaction_id: 'TX2', source_account_id: 'AC3', destination_account_id: 'AC4', transaction_timestamp: '2026-01-02T11:00:00', amount: 900, transaction_type: 'MERCHANT_PAYMENT', model_risk: null }], total: 2, page: 1, page_size: 50 },
    })
    renderAt(<Transactions />, '/transactions', '/transactions')
    const table = await screen.findByRole('table', { name: 'Transactions' })
    expect(within(table).getByText('not scored')).toBeInTheDocument()
    expect(within(table).getByText('0.770')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/Min amount/i), '100000')
    await waitFor(() => expect(calls.some((c) => c.url.includes('min_amount=100000'))).toBe(true))
    await userEvent.click(within(table).getByRole('button', { name: /Amount/ }))
    await waitFor(() => expect(calls.some((c) => c.url.includes('sort=amount'))).toBe(true))
  })
})
