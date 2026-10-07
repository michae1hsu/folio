import React from 'react';

export const gradingName=provider=>provider==='openai_decisions'?'OpenAI Decisions':'JEV';

export default function GradingProvider({value='jev',onChange,disabled=false,label='Grading provider'}){
  return <label>{label}<select aria-label={label} value={value} onChange={e=>onChange(e.target.value)} disabled={disabled}>
    <option value="jev">JEV</option><option value="openai_decisions">OpenAI Decisions · gpt-6-luna</option>
  </select></label>;
}
