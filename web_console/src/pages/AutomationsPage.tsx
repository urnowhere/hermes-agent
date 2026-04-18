import { useEffect, useState } from 'react';
import { apiClient } from '../lib/api';
import { toastStore } from '../store/toastStore';
import { CronList } from '../components/cron/CronList';
import { LoadingSpinner } from '../components/shared/LoadingSpinner';

interface CronJob {
  job_id: string;
  name: string;
  schedule: string;
  paused: boolean;
  prompt?: string;
  deliver?: string;
}

interface CronJobsResponse {
  ok: boolean;
  jobs?: CronJob[];
}

export function AutomationsPage() {
  const [jobs, setJobs] = useState<Array<{ id: string; name: string; schedule: string; status: string; prompt?: string; deliver?: string }>>([]);
  const [loading, setLoading] = useState(true);

  const refreshJobs = async () => {
    try {
      const response = await apiClient.get<CronJobsResponse>('/cron/jobs');
      if (response.ok && response.jobs) {
        setJobs(
          response.jobs.map((job) => ({
            id: job.job_id,
            name: job.name,
            schedule: job.schedule,
            status: job.paused ? 'paused' : 'active',
            prompt: job.prompt,
            deliver: job.deliver,
          }))
        );
      }
    } catch (err) {
      toastStore.error('Automations Load Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshJobs();
  }, []);

  const handleCreate = async (data: { name: string; schedule: string; prompt: string }) => {
    try {
      await apiClient.post('/cron/jobs', data);
      toastStore.success('Job created');
      await refreshJobs();
    } catch (err) { toastStore.error('Creation failed', err instanceof Error ? err.message : String(err)); }
  };

  const handleUpdate = async (id: string, data: Partial<{ name: string; schedule: string; prompt: string; deliver: string }>) => {
    try {
      await apiClient.patch(`/cron/jobs/${id}`, data);
      toastStore.success('Job updated');
      await refreshJobs();
    } catch (err) { toastStore.error('Update failed', err instanceof Error ? err.message : String(err)); }
  };

  const handlePause = async (id: string) => {
    try {
      await apiClient.post(`/cron/jobs/${id}/pause`, {});
      toastStore.info('Job paused');
      await refreshJobs();
    } catch (err) { toastStore.error('Pause failed', err instanceof Error ? err.message : String(err)); }
  };

  const handleResume = async (id: string) => {
    try {
      await apiClient.post(`/cron/jobs/${id}/resume`, {});
      toastStore.info('Job resumed');
      await refreshJobs();
    } catch (err) { toastStore.error('Resume failed', err instanceof Error ? err.message : String(err)); }
  };

  const handleRun = async (id: string) => {
    try {
      await apiClient.post(`/cron/jobs/${id}/run`, {});
      toastStore.success('Job started');
      await refreshJobs();
    } catch (err) { toastStore.error('Run failed', err instanceof Error ? err.message : String(err)); }
  };

  const handleDelete = async (id: string) => {
    try {
      await apiClient.del(`/cron/jobs/${id}`, {});
      toastStore.success('Job deleted');
      await refreshJobs();
    } catch (err) { toastStore.error('Delete failed', err instanceof Error ? err.message : String(err)); }
  };

  if (loading) {
    return <LoadingSpinner message="Loading automations…" />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flex: 1, overflowY: 'auto', minHeight: 0, paddingBottom: '30px', paddingRight: '12px' }}>
      <CronList
        jobs={jobs}
        onCreate={handleCreate}
        onUpdate={handleUpdate}
        onPause={handlePause}
        onResume={handleResume}
        onRun={handleRun}
        onDelete={handleDelete}
      />
    </div>
  );
}
