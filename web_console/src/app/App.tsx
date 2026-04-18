import { AppShell } from '../components/layout/AppShell';
import { ErrorBoundary } from '../components/shared/ErrorBoundary';
import { ConnectionProvider } from '../lib/connectionContext';
import { ConnectGate } from '../components/connect/ConnectScreen';

export function App() {
  return (
    <ErrorBoundary>
      <ConnectionProvider>
        <ConnectGate>
          <AppShell />
        </ConnectGate>
      </ConnectionProvider>
    </ErrorBoundary>
  );
}
