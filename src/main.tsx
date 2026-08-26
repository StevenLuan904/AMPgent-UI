import { createRoot } from 'react-dom/client'
import '@xyflow/react/dist/style.css'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import './styles.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <App />,
)
