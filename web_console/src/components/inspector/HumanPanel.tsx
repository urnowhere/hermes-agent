import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';

interface HumanRequest {
  request_id: string;
  type: 'approval' | 'clarification';
  prompt?: string;
  tool_name?: string;
  command?: string;
  question?: string;
  choices?: string[];
  status: 'pending' | 'resolved' | 'denied';
  created_at: number;
}

export function HumanPanel() {
  const [requests, setRequests] = useState<HumanRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [clarifyInput, setClarifyInput] = useState<{ [id: string]: string }>({});

  const fetchPending = async () => {
    try {
      const res = await apiClient.get<{ ok: boolean; pending: HumanRequest[] }>('/human/pending');
      if (res.ok) {
        setRequests(res.pending || []);
      }
    } catch (err) {
      console.error('Failed to load pending human requests:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPending();
    const interval = setInterval(fetchPending, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleApprove = async (id: string, decision: 'once' | 'session' | 'always' = 'once') => {
    try {
      await apiClient.post('/human/approve', { request_id: id, decision });
      fetchPending();
    } catch (err) {
      console.error(err);
    }
  };

  const handleDeny = async (id: string) => {
    try {
      await apiClient.post('/human/deny', { request_id: id });
      fetchPending();
    } catch (err) {
      console.error(err);
    }
  };

  const handleClarify = async (id: string, response: string) => {
    try {
      await apiClient.post('/human/clarify', { request_id: id, response });
      setClarifyInput(prev => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      fetchPending();
    } catch (err) {
      console.error(err);
    }
  };

  if (loading && requests.length === 0) {
    return <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>Loading pending requests...</div>;
  }

  if (requests.length === 0) {
    return <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>No pending approvals or questions.</div>;
  }

  return (
    <div style={{ padding: '16px', color: '#f8fafc', display: 'flex', flexDirection: 'column', gap: '16px', overflowY: 'auto', height: '100%' }}>
      <h3 style={{ margin: '0 0 8px 0', fontSize: '1rem', color: '#e2e8f0' }}>Pending Action Needed</h3>
      
      {requests.map(req => (
        <div key={req.request_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(234, 179, 8, 0.3)', padding: '12px', borderRadius: '6px' }}>
          
          {req.type === 'approval' ? (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontWeight: 600, color: '#facc15' }}>Security Approval</span>
                <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                  {new Date(req.created_at * 1000).toLocaleTimeString()}
                </span>
              </div>
              
              <div style={{ fontSize: '0.875rem', marginBottom: '8px' }}>
                The agent wants to use <strong>{req.tool_name}</strong>.
              </div>
              
              {req.command && (
                <div style={{ background: '#000', padding: '8px', borderRadius: '4px', fontFamily: 'monospace', fontSize: '0.8rem', color: '#4ade80', marginBottom: '12px', overflowX: 'auto' }}>
                  {req.command}
                </div>
              )}
              
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button 
                  onClick={() => handleApprove(req.request_id, 'once')}
                  style={{ background: 'rgba(34, 197, 94, 0.2)', color: '#4ade80', border: '1px solid rgba(34, 197, 94, 0.3)', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', flex: 1 }}
                >
                  Approve Once
                </button>
                <button 
                  onClick={() => handleApprove(req.request_id, 'session')}
                  style={{ background: 'rgba(34, 197, 94, 0.1)', color: '#4ade80', border: '1px solid rgba(34, 197, 94, 0.3)', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', flex: 1 }}
                >
                  Approve Session
                </button>
                <button 
                  onClick={() => handleDeny(req.request_id)}
                  style={{ background: 'rgba(239, 68, 68, 0.2)', color: '#f87171', border: '1px solid rgba(239, 68, 68, 0.3)', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', flex: 1 }}
                >
                  Deny
                </button>
              </div>
            </div>
          ) : (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontWeight: 600, color: '#60a5fa' }}>Clarification Needed</span>
                <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                  {new Date(req.created_at * 1000).toLocaleTimeString()}
                </span>
              </div>
              
              <div style={{ fontSize: '0.875rem', marginBottom: '12px' }}>
                {req.question}
              </div>
              
              {req.choices && req.choices.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {req.choices.map(choice => (
                    <button 
                      key={choice}
                      onClick={() => handleClarify(req.request_id, choice)}
                      style={{ background: 'rgba(255,255,255,0.05)', color: '#e2e8f0', border: '1px solid rgba(255,255,255,0.1)', padding: '8px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.85rem', textAlign: 'left' }}
                    >
                      {choice}
                    </button>
                  ))}
                  <button 
                    onClick={() => handleDeny(req.request_id)}
                    style={{ background: 'rgba(239, 68, 68, 0.1)', color: '#f87171', border: '1px dashed rgba(239, 68, 68, 0.3)', padding: '8px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.85rem' }}
                  >
                    Cancel Action
                  </button>
                </div>
              ) : (
                <div style={{ display: 'flex', gap: '8px' }}>
                  <input 
                    type="text" 
                    value={clarifyInput[req.request_id] || ''}
                    onChange={e => setClarifyInput({ ...clarifyInput, [req.request_id]: e.target.value })}
                    placeholder="Type your answer..."
                    onKeyDown={e => {
                      if (e.key === 'Enter' && clarifyInput[req.request_id]) {
                        handleClarify(req.request_id, clarifyInput[req.request_id]);
                      }
                    }}
                    style={{ flex: 1, padding: '8px 12px', background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', borderRadius: '4px', fontSize: '0.875rem' }}
                  />
                  <button 
                    onClick={() => handleClarify(req.request_id, clarifyInput[req.request_id] || '')}
                    disabled={!clarifyInput[req.request_id]}
                    style={{ background: '#3b82f6', color: '#fff', border: 'none', padding: '0 16px', borderRadius: '4px', cursor: clarifyInput[req.request_id] ? 'pointer' : 'not-allowed', opacity: clarifyInput[req.request_id] ? 1 : 0.5 }}
                  >
                    Send
                  </button>
                </div>
              )}
            </div>
          )}
          
        </div>
      ))}
    </div>
  );
}
