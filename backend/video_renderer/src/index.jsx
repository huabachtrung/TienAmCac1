import React from "react";
import {createRoot} from "react-dom/client";
import {
  AbsoluteFill,
  Composition,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  Video,
} from "remotion";

export const RemotionRoot = () => {
  return (
    <Composition
      id="EditedVideo"
      component={EditedVideo}
      durationInFrames={900}
      fps={30}
      width={1080}
      height={1920}
      calculateMetadata={async ({props}) => {
        const plan = props.plan;
        const fps = 30;
        const vertical = plan.orientation !== "horizontal";
        return {
          durationInFrames: Math.max(1, Math.ceil((plan.duration_sec || 1) * fps)),
          fps,
          width: vertical ? 1080 : 1920,
          height: vertical ? 1920 : 1080,
          props: {...props, plan},
        };
      }}
    />
  );
};

const EditedVideo = ({source, plan}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const time = frame / fps;
  const vertical = plan.orientation !== "horizontal";
  const captions = plan.captions || [];
  const popups = plan.text_popups || [];
  const icons = plan.icons || [];
  const activeCaption = captions.find((cue) => time >= cue.start && time <= cue.end);

  const zoom = 1 + 0.035 * Math.sin(time * 0.8);
  const shakeCue = (plan.camera || []).find(
    (cue) => cue.effect === "impact_shake" && time >= cue.start && time <= cue.end
  );
  const shake = shakeCue ? Math.sin(frame * 1.7) * 8 : 0;

  return (
    <AbsoluteFill style={{backgroundColor: "#030712", overflow: "hidden"}}>
      <Video
        src={source}
        style={{
          width: "100%",
          height: "100%",
          objectFit: vertical ? "cover" : "cover",
          transform: `scale(${zoom}) translateX(${shake}px)`,
          filter: "contrast(1.08) saturate(1.12)",
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(0,0,0,0.18) 0%, transparent 24%, transparent 68%, rgba(0,0,0,0.34) 100%)",
        }}
      />
      {popups.map((cue, index) => (
        <Popup cue={cue} key={`popup-${index}`} />
      ))}
      {icons.map((cue, index) => (
        <IconCue cue={cue} key={`icon-${index}`} />
      ))}
      {activeCaption ? <Caption cue={activeCaption} /> : null}
    </AbsoluteFill>
  );
};

const Caption = ({cue}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const time = frame / fps;
  const words = cue.words?.length ? cue.words : cue.text.split(/\s+/).map((text) => ({text}));

  return (
    <div
      style={{
        position: "absolute",
        left: "7%",
        right: "7%",
        bottom: "9%",
        textAlign: "center",
        fontFamily: "Arial, sans-serif",
        fontSize: 58,
        lineHeight: 1.08,
        fontWeight: 900,
        color: "white",
        textShadow: "0 5px 0 #000, 0 0 22px rgba(0,0,0,0.65)",
        WebkitTextStroke: "3px #111827",
      }}
    >
      {words.slice(0, 18).map((word, index) => {
        const active =
          typeof word.start === "number" && typeof word.end === "number"
            ? time >= word.start && time <= word.end
            : false;
        return (
          <span
            key={`${word.text}-${index}`}
            style={{
              display: "inline-block",
              margin: "0 6px",
              color: active ? "#facc15" : "white",
              transform: active ? "scale(1.12)" : "scale(1)",
            }}
          >
            {word.text}
          </span>
        );
      })}
    </div>
  );
};

const Popup = ({cue}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const progress = Math.min(1, Math.max(0, (frame / fps - cue.start) / Math.max(0.1, cue.end - cue.start)));
  const pop = spring({frame: progress * 18, fps, config: {damping: 9, stiffness: 130}});
  const pos = position(cue.position, width, height);
  const rotate = cue.effect === "shake" ? Math.sin(frame * 1.4) * 4 : 0;
  const opacity = interpolate(progress, [0, 0.1, 0.86, 1], [0, 1, 1, 0]);

  return (
    <div
      style={{
        position: "absolute",
        left: pos.x,
        top: pos.y,
        transform: `translate(-50%, -50%) scale(${0.82 + pop * 0.22}) rotate(${rotate}deg)`,
        opacity,
        padding: "14px 24px",
        borderRadius: 18,
        background: "rgba(2, 6, 23, 0.76)",
        border: "3px solid rgba(250, 204, 21, 0.95)",
        color: "#facc15",
        fontFamily: "Arial, sans-serif",
        fontSize: 68,
        fontWeight: 950,
        letterSpacing: 0,
        textShadow: "0 4px 0 #000",
        boxShadow: "0 18px 45px rgba(0,0,0,0.35)",
      }}
    >
      {cue.text}
    </div>
  );
};

const IconCue = ({cue}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const progress = Math.min(1, Math.max(0, (frame / fps - cue.start) / Math.max(0.1, cue.end - cue.start)));
  const pop = spring({frame: progress * 16, fps, config: {damping: 8, stiffness: 150}});
  const pos = position(cue.position, width, height);
  return (
    <div
      style={{
        position: "absolute",
        left: pos.x,
        top: pos.y,
        transform: `translate(-50%, -50%) scale(${0.7 + pop * 0.55})`,
        opacity: interpolate(progress, [0, 0.12, 0.8, 1], [0, 1, 1, 0]),
        fontSize: 92,
        fontWeight: 900,
        color: "#38bdf8",
        textShadow: "0 5px 0 #020617, 0 0 30px rgba(56,189,248,0.55)",
      }}
    >
      {cue.icon}
    </div>
  );
};

const position = (name, width, height) => {
  if (name === "upper") return {x: width / 2, y: height * 0.22};
  if (name === "side") return {x: width * 0.78, y: height * 0.36};
  return {x: width / 2, y: height * 0.42};
};

createRoot(document.getElementById("root")).render(<RemotionRoot />);
