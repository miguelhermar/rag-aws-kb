"use client";

interface ConfidenceBadgeProps {
  score: number;
}

export function ConfidenceBadge({ score }: ConfidenceBadgeProps) {
  const percentage = Math.round(score * 100);
  
  let colorClass = "bg-red-500";
  let textColor = "text-red-700 dark:text-red-400";
  
  if (score > 0.7) {
    colorClass = "bg-green-500";
    textColor = "text-green-700 dark:text-green-400";
  } else if (score > 0.4) {
    colorClass = "bg-yellow-500";
    textColor = "text-yellow-700 dark:text-yellow-400";
  }

  return (
    <div className="flex items-center gap-2">
      <span className={`text-xs font-semibold ${textColor}`}>
        Confidence: {percentage}%
      </span>
      <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full ${colorClass} transition-all duration-300`}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  );
}

