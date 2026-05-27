import { cn } from "@/lib/utils";

type SavantLogoProps = {
  size?: number;
  className?: string;
  rounded?: "lg" | "xl" | "2xl" | "full";
};

export function SavantLogo({
  size = 28,
  className,
  rounded = "lg",
}: SavantLogoProps) {
  const roundedClass =
    rounded === "full"
      ? "rounded-full"
      : rounded === "2xl"
      ? "rounded-2xl"
      : rounded === "xl"
      ? "rounded-xl"
      : "rounded-lg";

  return (
    <div
      className={cn(
        roundedClass,
        "bg-gradient-to-br from-blue-600 to-blue-700 flex items-center justify-center text-white font-extrabold shadow-sm select-none",
        className
      )}
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.62),
        lineHeight: 1,
      }}
      aria-label="Savantsdatabas"
      role="img"
    >
      S
    </div>
  );
}
