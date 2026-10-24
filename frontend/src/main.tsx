import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { DatasetProvider } from './hooks/DatasetContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <DatasetProvider>
        <App />
      </DatasetProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
