# ElevenLabs interview agent setup

Inkwell uses one private ElevenLabs managed agent. The application, not the interview guest,
selects this agent through `VOICE_INTERVIEW_PROVIDER` and `ELEVENLABS_AGENT_ID`.

## Agent prompt

Set the agent system prompt to exactly:

```text
{{interview_instructions}}
```

Inkwell supplies the complete interview rules, participant context, question plan, and bounded
resume transcript as the `interview_instructions` dynamic variable for every conversation. Enable
dynamic-variable overrides for the agent. The application also supplies `participant_name`,
`client_name`, `writer_name`, `article_title`, and `is_resumed` for optional voice or analytics use.

Do not add a second independent question plan in the ElevenLabs dashboard; doing so can make the
two providers behave differently.

## Client tool

Create a blocking client tool with this definition:

- Name: `end_interview`
- Description: `Finish the interview after thanking the participant.`
- Parameter: `reason`, required string enum with values `participant_finished` and
  `questions_complete`
- Wait for response / blocking: enabled

The browser validates the reason, waits briefly for final audio, flushes the canonical Inkwell
transcript, and then runs the existing transcript finalization and insight workflow.

## Authentication and webhook

1. Keep the agent private/authenticated.
2. Configure a workspace or agent `post_call_transcription` webhook pointing to
   `https://<backend-host>/api/v1/webhooks/elevenlabs`.
3. Store its HMAC secret as `ELEVENLABS_WEBHOOK_SECRET`.
4. Do not enable post-call audio delivery; Inkwell does not store call recordings.
5. Use the ElevenLabs `staging` environment for an Inkwell staging deployment and `production`
   otherwise. The backend chooses this automatically when it requests a WebRTC token.

## Activation

Leave production on `VOICE_INTERVIEW_PROVIDER=openai_live` until staging validation passes. Switch
new calls to ElevenLabs by changing only:

```env
VOICE_INTERVIEW_PROVIDER=elevenlabs
```

Restart the backend after changing the setting. Existing calls are not moved between providers.
