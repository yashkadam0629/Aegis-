import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './layouts/AppLayout'
import Overview from './pages/Overview'
import Transactions from './pages/Transactions'
import NetworkExplorer from './pages/NetworkExplorer'
import Alerts from './pages/Alerts'
import Intelligence from './pages/Intelligence'
import Investigations from './pages/Investigations'
import InvestigationWorkspace from './pages/InvestigationWorkspace'
import Models from './pages/Models'
import Insights from './pages/Insights'
import DataManagement from './pages/DataManagement'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Overview />} />
        <Route path="transactions" element={<Transactions />} />
        <Route path="network" element={<NetworkExplorer />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="intelligence" element={<Intelligence />} />
        <Route path="investigations" element={<Investigations />} />
        <Route path="investigations/:id" element={<InvestigationWorkspace />} />
        <Route path="models" element={<Models />} />
        <Route path="insights" element={<Insights />} />
        <Route path="data" element={<DataManagement />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
