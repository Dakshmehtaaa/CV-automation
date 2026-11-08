"use client";

import { useEffect, useRef, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { createSwapy, type Config, type SlotItemMapArray, type Swapy } from "swapy";
import { cn } from "@/lib/utils";
import {Icon} from "@/components/Icon";

type LayoutConfig = Partial<Omit<Config, "manualSwap" | "enabled">>;

export type SwapyLayoutProps = {
  id: string;
  map: SlotItemMapArray;
  enable?: boolean;
  reducedMotion?: boolean;
  onSwap?: (map: SlotItemMapArray) => void;
  config?: LayoutConfig;
  className?: string;
  children: ReactNode;
};

const mapKey = (map: SlotItemMapArray) => JSON.stringify(map.map(({ slot, item }) => [slot, item]));

export function SwapyLayout({ id, map, enable = true, reducedMotion = false, onSwap, config, className, children }: SwapyLayoutProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const swapyRef = useRef<Swapy | null>(null);
  const onSwapRef = useRef(onSwap);
  const mapRef = useRef(map);
  const enabledRef = useRef(enable);
  const syncRef = useRef<(() => void) | null>(null);

  // Primitive option values keep inline config objects from rebuilding the instance.
  const options: LayoutConfig = {
    animation: reducedMotion ? "none" : config?.animation ?? "dynamic",
    swapMode: config?.swapMode ?? "hover",
    dragOnHold: config?.dragOnHold ?? false,
    autoScrollOnDrag: config?.autoScrollOnDrag ?? false,
    dragAxis: config?.dragAxis ?? "both",
  };
  const optionsKey = JSON.stringify(options);
  const optionsRef = useRef({ options, key: optionsKey });
  onSwapRef.current = onSwap;
  mapRef.current = map;
  enabledRef.current = enable;
  optionsRef.current = { options, key: optionsKey };
  const currentMapKey = mapKey(map);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let disposed = false;
    let dragging = false;
    let pending = false;
    let updating = false;
    let frame: number | null = null;
    let appliedOptionsKey = optionsRef.current.key;

    function attach() {
      const instance = createSwapy(container!, {
        ...optionsRef.current.options,
        enabled: enabledRef.current,
        manualSwap: true,
      });
      swapyRef.current = instance;
      appliedOptionsKey = optionsRef.current.key;
      instance.onSwapStart(() => { dragging = true; });
      instance.onSwap((event) => {
        // Swapy mutates its own map on the next swap; React gets an owned copy.
        onSwapRef.current?.(event.newSlotItemMap.asArray.map(({ slot, item }) => ({ slot, item })));
      });
      instance.onSwapEnd(() => {
        dragging = false;
        if (pending) scheduleSync();
      });
      return instance;
    }

    function scheduleSync() {
      pending = true;
      if (disposed || dragging || updating || frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        if (disposed || dragging) return;
        pending = false;
        const instance = swapyRef.current;
        if (!instance) return;

        if (appliedOptionsKey !== optionsRef.current.key) {
          instance.destroy();
          attach();
          return;
        }

        // Pointer swaps already update Swapy's map and views. Rebuilding here
        // would interrupt the gesture; only an external reorder needs update().
        if (mapKey(instance.slotItemMap().asArray) === mapKey(mapRef.current)) return;
        updating = true;
        instance.update();
        requestAnimationFrame(() => {
          updating = false;
          // update() schedules its own frame. Destroy again after that frame
          // if React unmounted, so it cannot leave rebuilt listeners behind.
          if (disposed) instance.destroy();
          else if (pending || appliedOptionsKey !== optionsRef.current.key) scheduleSync();
        });
      });
    }

    attach();
    syncRef.current = scheduleSync;
    return () => {
      disposed = true;
      if (frame !== null) cancelAnimationFrame(frame);
      syncRef.current = null;
      swapyRef.current?.destroy();
      swapyRef.current = null;
    };
  }, []);

  useEffect(() => {
    swapyRef.current?.enable(enable);
  }, [enable]);

  useEffect(() => {
    syncRef.current?.();
  }, [currentMapKey, optionsKey]);

  return <div id={id} ref={containerRef} className={className}>{children}</div>;
}

type SwapyElementProps = Omit<HTMLAttributes<HTMLDivElement>, "id" | "children"> & {
  id: string;
  children: ReactNode;
};

export function SwapySlot({ id, className, children, ...props }: SwapyElementProps) {
  return <div {...props} className={cn("cd-swapy-slot", className)} data-swapy-slot={id}>{children}</div>;
}

export function SwapyItem({ id, className, children, ...props }: SwapyElementProps) {
  return <div {...props} className={cn("cd-swapy-item", className)} data-swapy-item={id}>{children}</div>;
}

export type DragHandleProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  label?: string;
};

export function DragHandle({ label, className, type = "button", "aria-label": ariaLabel, ...props }: DragHandleProps) {
  return (
    <button
      {...props}
      type={type}
      data-swapy-handle
      className={cn("cd-drag-handle cursor-interaction", className)}
      aria-label={ariaLabel ?? label ?? "Reorder card"}
    >
      <Icon name="grip-vertical"/>
    </button>
  );
}
