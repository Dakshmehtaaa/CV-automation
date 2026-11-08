import type { MetricDonutDatum } from './components/MetricDonut';
import type { Period } from './types';

export function languageChart(current: Period['language'], allTime: Period['language']) {
  const fr = current.fr ?? 0;
  const en = current.en ?? 0;
  const unknown = current.unknown ?? 0;
  const currentKnown = fr + en;
  const ratioCounts = currentKnown > 0 ? current : allTime;
  const known = (ratioCounts.fr ?? 0) + (ratioCounts.en ?? 0);
  const estimated = unknown > 0 && known > 0;
  const allocatedFrench = estimated ? Math.round(unknown * (ratioCounts.fr ?? 0) / known) : 0;
  const data: MetricDonutDatum[] = [
    { id: 'fr', label: 'French', value: fr + allocatedFrench, color: '#398fae' },
    { id: 'en', label: 'English', value: en + (estimated ? unknown - allocatedFrench : 0), color: '#8c6ad1' },
  ];
  if (unknown > 0 && !estimated) data.push({ id: 'unknown', label: 'Other / unknown', value: unknown, color: '#d5a23b' });
  const note = estimated
    ? `Estimated split: ${unknown.toLocaleString('en-GB')} unclassified descriptions follow the ${currentKnown > 0 ? 'recorded' : 'all-time recorded'} French/English ratio.`
    : unknown > 0 ? 'No recorded French/English ratio yet; unclassified descriptions stay unknown.' : '';
  return { data, estimated, note };
}
