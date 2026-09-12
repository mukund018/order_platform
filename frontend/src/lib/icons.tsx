/**
 * A tiny, deterministic icon per product category — no external image host, no
 * broken-image state, no network request. tools/seed.py generates every product name
 * from a fixed list of item nouns (`ITEMS` in that file); this is the matching icon for
 * each one, picked by keyword, so the storefront's product cards show something more
 * specific than a flat colour block without needing real product photography for
 * products that do not exist.
 *
 * Every icon shares one visual language: a 24x24 viewBox, no fill, a 1.5-width stroke
 * in `currentColor` so it inherits whatever text colour the Swatch places it on.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Base({ children, ...props }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {children}
    </svg>
  );
}

function Kettle(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M5 10a7 7 0 0 1 14 0c0 5-2 8-2 9H7c0-1-2-4-2-9Z" />
      <path d="M9 5.5c1-2 5-2 6 0" />
      <path d="M17 11h2.5" />
      <path d="M4.5 11H7" />
    </Base>
  );
}

function TravelMug(props: IconProps) {
  return (
    <Base {...props}>
      <rect x="7" y="6" width="9" height="14" rx="2.5" />
      <path d="M16 9h1.5a1.5 1.5 0 0 1 1.5 1.5v2A1.5 1.5 0 0 1 17.5 14H16" />
      <path d="M9.5 3.5h4l-.6 2.5h-2.8Z" />
    </Base>
  );
}

function DeskLamp(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M6 5.5 15 8l-1 3-9-2.5Z" />
      <path d="M13 8.8 9 20" />
      <path d="M5 20h8" />
      <circle cx="17" cy="5" r="1" fill="currentColor" stroke="none" />
    </Base>
  );
}

function Notebook(props: IconProps) {
  return (
    <Base {...props}>
      <rect x="5" y="3.5" width="14" height="17" rx="1.5" />
      <path d="M9 3.5v17" />
      <path d="M12.5 8h4M12.5 11h4M12.5 14h3" />
    </Base>
  );
}

function WaterBottle(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M10 2.5h4v3l1.5 2v12a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2V7.5l1.5-2Z" />
      <path d="M9.5 13h5" />
    </Base>
  );
}

function CableOrganiser(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M4 12c0-3 2-5 5-5s5 2 5 5-2 5-5 5" />
      <path d="M9 17a5 5 0 0 0 5-5c0-1.7.8-3 2.5-3S20 10.3 20 12" />
      <circle cx="20" cy="12" r="1" fill="currentColor" stroke="none" />
    </Base>
  );
}

function ChoppingBoard(props: IconProps) {
  return (
    <Base {...props}>
      <rect x="3.5" y="6" width="17" height="12" rx="2.5" />
      <circle cx="17.5" cy="9.5" r="1" />
    </Base>
  );
}

function StorageBox(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M4 8.5 12 5l8 3.5-8 3.5Z" />
      <path d="M4 8.5v7L12 19l8-3.5v-7" />
      <path d="M12 12v7" />
    </Base>
  );
}

function PenStand(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M7 21c-1.5-4-1.5-9-1-13h8c.5 4 .5 9-1 13Z" />
      <path d="M10 3.5 16.5 8l-2.3 1.6L8 5Z" />
    </Base>
  );
}

function LaptopSleeve(props: IconProps) {
  return (
    <Base {...props}>
      <rect x="3.5" y="6.5" width="17" height="11" rx="1.5" />
      <path d="M3.5 10h17" />
    </Base>
  );
}

function WallClock(props: IconProps) {
  return (
    <Base {...props}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l3 2" />
    </Base>
  );
}

function SpiceJar(props: IconProps) {
  return (
    <Base {...props}>
      <rect x="7" y="8" width="10" height="12" rx="2" />
      <path d="M9.5 8V5.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V8" />
      <path d="M7 13h10" />
    </Base>
  );
}

function ToteBag(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M5.5 8h13l1 12h-15Z" />
      <path d="M9 8V6a3 3 0 0 1 6 0v2" />
    </Base>
  );
}

function PhoneStand(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M4.5 19h15" />
      <path d="M6 19c0-3 1-5 3-6.5l8-4" />
      <rect x="13.7" y="6.3" width="6" height="10.5" rx="1.3" transform="rotate(20 16.7 11.5)" />
    </Base>
  );
}

function ServingTray(props: IconProps) {
  return (
    <Base {...props}>
      <rect x="3" y="8" width="18" height="9" rx="2" />
      <path d="M6 8V6.5A1.5 1.5 0 0 1 7.5 5h9A1.5 1.5 0 0 1 18 6.5V8" />
    </Base>
  );
}

/** Anything that does not match a known item noun still gets a consistent, deliberate
 * icon rather than an empty swatch - a generic package, since every product here is
 * something that ships in a box. */
function Package(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M4 8.5 12 5l8 3.5-8 3.5Z" />
      <path d="M4 8.5v7L12 19l8-3.5v-7" />
      <path d="M12 12v7M4 8.5 12 12l8-3.5" />
    </Base>
  );
}

/** Keyword → icon, matched against the product name case-insensitively. Order matters
 * only in that a more specific phrase should be listed before a substring of it would
 * otherwise match something else - none of these currently overlap. */
const ICONS_BY_KEYWORD: [string, (props: IconProps) => React.JSX.Element][] = [
  ["kettle", Kettle],
  ["travel mug", TravelMug],
  ["desk lamp", DeskLamp],
  ["notebook", Notebook],
  ["water bottle", WaterBottle],
  ["cable organiser", CableOrganiser],
  ["chopping board", ChoppingBoard],
  ["storage box", StorageBox],
  ["pen stand", PenStand],
  ["laptop sleeve", LaptopSleeve],
  ["wall clock", WallClock],
  ["spice jar", SpiceJar],
  ["tote bag", ToteBag],
  ["phone stand", PhoneStand],
  ["serving tray", ServingTray],
];

export function pickIcon(productName: string): (props: IconProps) => React.JSX.Element {
  const lower = productName.toLowerCase();
  for (const [keyword, Icon] of ICONS_BY_KEYWORD) {
    if (lower.includes(keyword)) return Icon;
  }
  return Package;
}
