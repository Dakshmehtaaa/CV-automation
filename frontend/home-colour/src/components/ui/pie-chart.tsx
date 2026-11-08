"use client";

import * as React from "react";
import * as RechartsPrimitive from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";
import { cn } from "@/lib/utils";

// The supplied chart wrapper supports both the default and dark palettes.
const THEMES = { light: "", dark: ".dark" } as const;

export type ChartConfig = Record<string, {
  label?: React.ReactNode;
  icon?: React.ComponentType;
} & (
  | { color?: string; theme?: never }
  | { color?: never; theme: Record<keyof typeof THEMES, string> }
)>;

const ChartContext = React.createContext<{ config: ChartConfig } | null>(null);

function useChart() {
  const context = React.useContext(ChartContext);
  if (!context) throw new Error("useChart must be used within a <ChartContainer />");
  return context;
}

type ChartContainerProps = Omit<React.ComponentProps<"div">, "children"> & {
  config: ChartConfig;
  children: React.ComponentProps<typeof RechartsPrimitive.ResponsiveContainer>["children"];
};

function ChartContainer({ id, className, children, config, ...props }: ChartContainerProps) {
  const uniqueId = React.useId();
  const chartId = `chart-${(id || uniqueId).replace(/[^a-zA-Z0-9_-]/g, "")}`;

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        data-slot="chart"
        data-chart={chartId}
        className={cn(
          "flex aspect-video justify-center text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line[stroke='#ccc']]:stroke-border/50 [&_.recharts-curve.recharts-tooltip-cursor]:stroke-border [&_.recharts-polar-grid_[stroke='#ccc']]:stroke-border [&_.recharts-radial-bar-background-sector]:fill-muted [&_.recharts-rectangle.recharts-tooltip-cursor]:fill-muted [&_.recharts-reference-line_[stroke='#ccc']]:stroke-border [&_.recharts-dot[stroke='#fff']]:stroke-transparent [&_.recharts-layer]:outline-none [&_.recharts-sector]:outline-none [&_.recharts-sector[stroke='#fff']]:stroke-transparent [&_.recharts-surface]:outline-none",
          className,
        )}
        {...props}
      >
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer width="100%" height="100%" minWidth={0}>
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  );
}

function ChartStyle({ id, config }: { id: string; config: ChartConfig }) {
  const colorConfig = Object.entries(config).filter(([, item]) => item.theme || item.color);
  if (!colorConfig.length) return null;

  const safeId = id.replace(/[^a-zA-Z0-9_-]/g, "");
  const css = Object.entries(THEMES).map(([theme, prefix]) => {
    const colors = colorConfig.map(([key, item]) => {
      const color = item.theme?.[theme as keyof typeof THEMES] || item.color;
      return color && /^[a-zA-Z0-9_-]+$/.test(key) ? `  --color-${key}: ${color};` : "";
    }).join("\n");
    return `${prefix} [data-chart="${safeId}"] {\n${colors}\n}`;
  }).join("\n");

  return <style>{css}</style>;
}

const ChartTooltip = RechartsPrimitive.Tooltip;

type ChartTooltipContentProps = RechartsPrimitive.TooltipProps<ValueType, NameType> &
  Omit<React.ComponentProps<"div">, "children" | "content"> & {
    hideLabel?: boolean;
    hideIndicator?: boolean;
    indicator?: "line" | "dot" | "dashed";
    nameKey?: string;
    labelKey?: string;
  };

function ChartTooltipContent({
  active, payload, className, indicator = "dot", hideLabel = false,
  hideIndicator = false, label, labelFormatter, labelClassName,
  formatter, color, nameKey, labelKey,
}: ChartTooltipContentProps) {
  const { config } = useChart();

  const tooltipLabel = React.useMemo(() => {
    if (hideLabel || !payload?.length) return null;
    const [item] = payload;
    const key = `${labelKey || item?.dataKey || item?.name || "value"}`;
    const itemConfig = getPayloadConfigFromPayload(config, item, key);
    const value = !labelKey && typeof label === "string"
      ? (config[label]?.label ?? label)
      : itemConfig?.label;

    if (labelFormatter) {
      return <div className={cn("font-medium", labelClassName)}>{labelFormatter(value, payload)}</div>;
    }
    if (value === undefined || value === null || value === "") return null;
    return <div className={cn("font-medium", labelClassName)}>{value}</div>;
  }, [label, labelFormatter, payload, hideLabel, labelClassName, config, labelKey]);

  if (!active || !payload?.length) return null;
  const nestLabel = payload.length === 1 && indicator !== "dot";

  return (
    <div className={cn("cd-tooltip grid min-w-[8rem] items-start gap-1.5 rounded-lg border border-border/50 bg-background px-2.5 py-1.5 text-xs shadow-xl", className)}>
      {!nestLabel ? tooltipLabel : null}
      <div className="grid gap-1.5">
        {payload.map((item, index) => {
          const key = `${nameKey || item.name || item.dataKey || "value"}`;
          const itemConfig = getPayloadConfigFromPayload(config, item, key);
          const datum = asRecord(item.payload);
          const indicatorColor = color || (typeof datum?.fill === "string" ? datum.fill : undefined) || item.color;
          const Icon = itemConfig?.icon;

          return (
            <div key={`${item.dataKey ?? item.name ?? "value"}-${index}`} className={cn("flex w-full flex-wrap items-stretch gap-2", indicator === "dot" && "items-center")}>
              {formatter && item.value !== undefined && item.name !== undefined ? (
                formatter(item.value, item.name, item, index, payload)
              ) : (
                <>
                  {Icon ? <Icon /> : !hideIndicator && (
                    <div
                      className={cn("shrink-0 rounded-[2px]", {
                        "h-2.5 w-2.5": indicator === "dot",
                        "w-1": indicator === "line",
                        "w-0 border-[1.5px] border-dashed": indicator === "dashed",
                        "my-0.5": nestLabel && indicator === "dashed",
                      })}
                      style={{ backgroundColor: indicator === "dashed" ? "transparent" : indicatorColor, borderColor: indicatorColor }}
                    />
                  )}
                  <div className={cn("flex flex-1 justify-between gap-3 leading-none", nestLabel ? "items-end" : "items-center")}>
                    <div className="grid gap-1.5">
                      {nestLabel ? tooltipLabel : null}
                      <span className="text-muted-foreground">{itemConfig?.label ?? item.name}</span>
                    </div>
                    {item.value !== undefined && item.value !== null && (
                      <span className="font-mono font-medium tabular-nums text-foreground">
                        {typeof item.value === "number" ? item.value.toLocaleString() : Array.isArray(item.value) ? item.value.join(" – ") : item.value}
                      </span>
                    )}
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const ChartLegend = RechartsPrimitive.Legend;

function ChartLegendContent({ className, hideIcon = false, payload, verticalAlign = "bottom", nameKey }:
  React.ComponentProps<"div"> & Pick<RechartsPrimitive.LegendProps, "payload" | "verticalAlign"> & {
    hideIcon?: boolean;
    nameKey?: string;
  }) {
  const { config } = useChart();
  if (!payload?.length) return null;

  return (
    <div className={cn("flex items-center justify-center gap-4", verticalAlign === "top" ? "pb-3" : "pt-3", className)}>
      {payload.map((item, index) => {
        const key = `${nameKey || item.dataKey || "value"}`;
        const itemConfig = getPayloadConfigFromPayload(config, item, key);
        const Icon = itemConfig?.icon;
        return (
          <div key={`${item.value}-${index}`} className="flex items-center gap-1.5">
            {Icon && !hideIcon ? <Icon /> : <div className="h-2 w-2 shrink-0 rounded-[2px]" style={{ backgroundColor: item.color }} />}
            {itemConfig?.label ?? item.value}
          </div>
        );
      })}
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : undefined;
}

function getPayloadConfigFromPayload(config: ChartConfig, payload: unknown, key: string) {
  const item = asRecord(payload);
  if (!item) return undefined;
  const datum = asRecord(item.payload);
  const configLabelKey = typeof item[key] === "string" ? item[key]
    : typeof datum?.[key] === "string" ? datum[key] : key;
  return config[configLabelKey as string] ?? config[key];
}

export { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent, ChartStyle };
