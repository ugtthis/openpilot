using Cxx = import "/include/c++.capnp";
$Cxx.namespace("cereal");

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

struct CamcorderControl @0x81c2f05a394cf4af {
  sequence @0 :UInt32;
  action @1 :Action;
  stream @2 :Stream;
  requestMonoTime @3 :UInt64;

  enum Action {
    idle @0;
    start @1;
    stop @2;
  }

  enum Stream {
    wideRoad @0;
    cabin @1;
  }
}

struct CamcorderState @0xaedffd8f31e7b55d {
  sequence @0 :UInt32;
  phase @1 :Phase;
  clipId @2 :Text;
  error @3 :Text;
  elapsedS @4 :Float32;
  audioSampleRate @5 :UInt32;
  audioChannels @6 :UInt16;
  audioGapCount @7 :UInt32;
  audioGapFrameCount @8 :UInt64;
  micName @9 :Text;
  notice @10 :Notice;

  enum Phase {
    idle @0;
    warming @1;
    recording @2;
    finalizing @3;
    failed @4;
  }

  enum Notice {
    none @0;
    storageFullSaved @1;
    storageFull @2;
    audioErrorSaved @3;
    recordingErrorSaved @4;
    recordingFailed @5;
    micDisconnected @6;
    micUnavailable @7;
    recordingRecovered @8;
  }
}

struct CustomReserved2 @0xf35cc4560bbf6ec2 {
}

struct CustomReserved3 @0xda96579883444c35 {
}

struct CustomReserved4 @0x80ae746ee2596b11 {
}

struct CustomReserved5 @0xa5cd762cd951a455 {
}

struct CustomReserved6 @0xf98d843bfd7004a3 {
}

struct CustomReserved7 @0xb86e6369214c01c8 {
}

struct CustomReserved8 @0xf416ec09499d9d19 {
}

struct CustomReserved9 @0xa1680744031fdb2d {
}

struct CustomReserved10 @0xcb9fd56c7057593a {
}

struct CustomReserved11 @0xc2243c65e0340384 {
}

struct CustomReserved12 @0x9ccdc8676701b412 {
}

struct CustomReserved13 @0xcd96dafb67a082d0 {
}

struct CustomReserved14 @0xb057204d7deadf3f {
}

struct CustomReserved15 @0xbd443b539493bc68 {
}

struct CustomReserved16 @0xfc6241ed8877b611 {
}

struct CustomReserved17 @0xa30662f84033036c {
}

struct CustomReserved18 @0xc86a3d38d13eb3ef {
}

struct CustomReserved19 @0xa4f1eb3323f5f582 {
}
