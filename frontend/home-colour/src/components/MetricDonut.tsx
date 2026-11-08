import * as React from "react";
import { Cell, Pie, PieChart } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/pie-chart";

export type MetricDonutDatum = { id: string; label: string; value: number; color: string };

export interface MetricDonutProps {
  data: MetricDonutDatum[];
  label: string;
  totalLabel: string;
}

const countFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 20 });
const percentFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

export const MetricDonut = React.memo(function MetricDonut({ data, label, totalLabel }: MetricDonutProps) {
  const titleId = React.useId();
  const total = data.reduce((sum, item) => sum + item.value, 0);
  const chartData = data.map(item => ({ ...item, fill: item.color }));
  const config: ChartConfig = Object.fromEntries(data.map(item => [item.id, { label: item.label, color: item.color }]));
  const positiveSegments = data.filter(item => item.value > 0).length;

  return (
    <div className="cd-donut" role="figure" aria-labelledby={titleId}>
      <span className="sr-only" id={titleId}>{label}. Total: {countFormat.format(total)} {totalLabel}.</span>
      <div className="cd-donut-plot">
        {total > 0 ? (
          <ChartContainer config={config} className="h-full w-full aspect-square" aria-hidden="true">
            <PieChart margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
              <ChartTooltip cursor={false} isAnimationActive={false} content={<ChartTooltipContent nameKey="id" hideLabel formatter={(value, _name, item) => {
                const count=typeof value==='number'?value:0;
                return <><span>{item.payload.label}</span><strong>{countFormat.format(count)} · {percentFormat.format(count/total*100)}%</strong></>;
              }}/>} />
              <Pie
                data={chartData}
                dataKey="value"
                nameKey="label"
                cx="50%"
                cy="50%"
                innerRadius="65%"
                outerRadius="92%"
                cornerRadius={9}
                paddingAngle={positiveSegments > 1 ? 3 : 0}
                stroke="none"
                isAnimationActive={false}
              >
                {data.map(item => <Cell key={item.id} fill={item.color} />)}
              </Pie>
            </PieChart>
          </ChartContainer>
        ) : <div className="cd-donut-empty" aria-hidden="true" />}
        <div className="cd-donut-center" aria-hidden="true">
          <strong>{countFormat.format(total)}</strong>
          <span>{totalLabel}</span>
        </div>
      </div>
      <ul className="cd-chart-breakdown cd-sr" aria-label={`${label} breakdown`}>
        {data.map(item => (
          <li className="cd-chart-row" key={item.id}>
            <span className="cd-swatch" style={{ backgroundColor: item.color }} aria-hidden="true" />
            <span>{item.label}</span>
            <strong>{countFormat.format(item.value)}</strong>
            <small>{percentFormat.format(total > 0 ? item.value / total * 100 : 0)}%</small>
          </li>
        ))}
        {data.length === 0 && <li className="cd-chart-empty">No data yet</li>}
      </ul>
      {total === 0 && <span className="sr-only">No items recorded yet. Total: 0 {totalLabel}.</span>}
    </div>
  );
},(previous,next)=>previous.label===next.label&&previous.totalLabel===next.totalLabel&&previous.data.length===next.data.length&&previous.data.every((item,i)=>{const other=next.data[i];return item.id===other.id&&item.label===other.label&&item.value===other.value&&item.color===other.color;}));
