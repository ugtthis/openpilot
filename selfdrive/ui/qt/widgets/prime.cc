#include "selfdrive/ui/qt/widgets/prime.h"

#include <QDebug>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QPushButton>
#include <QStackedWidget>
#include <QTimer>
#include <QVBoxLayout>

#include <QrCode.hpp>

#include "selfdrive/ui/qt/request_repeater.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/qt_window.h"
#include "selfdrive/ui/qt/widgets/wifi.h"

using qrcodegen::QrCode;

PairingQRWidget::PairingQRWidget(QWidget* parent) : QWidget(parent) {
  timer = new QTimer(this);
  connect(timer, &QTimer::timeout, this, &PairingQRWidget::refresh);
}

void PairingQRWidget::showEvent(QShowEvent *event) {
  refresh();
  timer->start(5 * 60 * 1000);
  device()->setOffroadBrightness(100);
}

void PairingQRWidget::hideEvent(QHideEvent *event) {
  timer->stop();
  device()->setOffroadBrightness(BACKLIGHT_OFFROAD);
}

void PairingQRWidget::refresh() {
  QString pairToken = CommaApi::create_jwt({{"pair", true}});
  QString qrString = "https://connect.comma.ai/?pair=" + pairToken;
  this->updateQrCode(qrString);
  update();
}

void PairingQRWidget::updateQrCode(const QString &text) {
  QrCode qr = QrCode::encodeText(text.toUtf8().data(), QrCode::Ecc::LOW);
  qint32 sz = qr.getSize();
  QImage im(sz, sz, QImage::Format_RGB32);

  QRgb black = qRgb(0, 0, 0);
  QRgb white = qRgb(255, 255, 255);
  for (int y = 0; y < sz; y++) {
    for (int x = 0; x < sz; x++) {
      im.setPixel(x, y, qr.getModule(x, y) ? black : white);
    }
  }

  // Integer division to prevent anti-aliasing
  int final_sz = ((width() / sz) - 1) * sz;
  img = QPixmap::fromImage(im.scaled(final_sz, final_sz, Qt::KeepAspectRatio), Qt::MonoOnly);
}

void PairingQRWidget::paintEvent(QPaintEvent *e) {
  QPainter p(this);
  p.fillRect(rect(), Qt::white);

  QSize s = (size() - img.size()) / 2;
  p.drawPixmap(s.width(), s.height(), img);
}


PairingPopup::PairingPopup(QWidget *parent) : DialogBase(parent) {
  QHBoxLayout *hlayout = new QHBoxLayout(this);
  hlayout->setContentsMargins(0, 0, 0, 0);
  hlayout->setSpacing(0);

  setStyleSheet("PairingPopup { background-color: #E0E0E0; }");

  // text
  QVBoxLayout *vlayout = new QVBoxLayout();
  vlayout->setContentsMargins(85, 70, 50, 70);
  vlayout->setSpacing(50);
  hlayout->addLayout(vlayout, 1);
  {
    QPushButton *close = new QPushButton(QIcon(":/icons/close.svg"), "", this);
    close->setIconSize(QSize(80, 80));
    close->setStyleSheet("border: none;");
    vlayout->addWidget(close, 0, Qt::AlignLeft);
    QObject::connect(close, &QPushButton::clicked, this, &QDialog::reject);

    vlayout->addSpacing(30);

    QLabel *title = new QLabel(tr("Pair your device to your comma account"), this);
    title->setStyleSheet("font-size: 75px; color: black;");
    title->setWordWrap(true);
    vlayout->addWidget(title);

    QLabel *instructions = new QLabel(QString(R"(
      <ol type='1' style='margin-left: 15px;'>
        <li style='margin-bottom: 50px;'>%1</li>
        <li style='margin-bottom: 50px;'>%2</li>
        <li style='margin-bottom: 50px;'>%3</li>
      </ol>
    )").arg(tr("Go to https://connect.comma.ai on your phone"))
    .arg(tr("Click \"add new device\" and scan the QR code on the right"))
    .arg(tr("Bookmark connect.comma.ai to your home screen to use it like an app")), this);

    instructions->setStyleSheet("font-size: 47px; font-weight: bold; color: black;");
    instructions->setWordWrap(true);
    vlayout->addWidget(instructions);

    vlayout->addStretch();
  }

  // QR code
  PairingQRWidget *qr = new PairingQRWidget(this);
  hlayout->addWidget(qr, 1);
}


PrimeDefaultWidget::PrimeDefaultWidget(QWidget *parent) : QPushButton(parent) {
  setObjectName("primeDefaultWidget");
  setFixedHeight(244);

  QHBoxLayout *mainLayout = new QHBoxLayout(this);
  mainLayout->setContentsMargins(55, 50, 55, 50);
  mainLayout->setSpacing(0);

  QVBoxLayout *textLayout = new QVBoxLayout();

  QLabel *notSubscribed = new QLabel(tr("NOT SUBSCRIBED"));
  notSubscribed->setStyleSheet("font-size: 36px; font-weight: bold; color: #FFEB88;");

  QLabel *wantToJoin = new QLabel(tr("Want to join?"));
  wantToJoin->setStyleSheet("font-size: 60px; font-weight: bold; color: white;");

  textLayout->addStretch();
  textLayout->addWidget(notSubscribed);
  textLayout->addWidget(wantToJoin);
  textLayout->addStretch();

  mainLayout->addLayout(textLayout);
  mainLayout->addStretch(); // Pushes btnIcon to the right

  QLabel *btnIcon = new QLabel("❯");
  btnIcon->setStyleSheet("font-size: 60px; font-weight: bold; color: white;");
  mainLayout->addWidget(btnIcon);

  setStyleSheet(R"(
    #primeDefaultWidget {
      border: 2px solid #F5D847;
      border-radius: 25px;
      background-color: #333333;
    }
    #primeDefaultWidget:pressed {
      background-color: #444444;
    }
  )");
}


PrimeSubscribedWidget::PrimeSubscribedWidget(QWidget *parent) : QFrame(parent) {
  setObjectName("primeWidget");
  QVBoxLayout *mainLayout = new QVBoxLayout(this);
  mainLayout->setContentsMargins(55, 50, 55, 50);
  mainLayout->setSpacing(3);

  QLabel *subscribed = new QLabel(tr("✓ SUBSCRIBED"));
  subscribed->setStyleSheet("font-size: 36px; font-weight: bold;");
  subscribed->setText(QString("<span style='color: #AAED70;'>✓</span> SUBSCRIBED"));

  QLabel *commaPrime = new QLabel(tr("comma prime"));
  commaPrime->setStyleSheet("font-size: 60px; font-weight: bold;");
  commaPrime->setText(QString("comma <span style='color: #AAED70;'>prime</span>"));

  // Add stretches to center the labels vertically
  mainLayout->addStretch();
  mainLayout->addWidget(subscribed);
  mainLayout->addWidget(commaPrime);
  mainLayout->addStretch();

  setStyleSheet(R"(
    #primeWidget {
      border-radius: 25px;
      background-color: #333333;
    }
  )");
}


PrimeAdWidget::PrimeAdWidget(QWidget* parent) : QFrame(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(80, 90, 80, 60);
  main_layout->setSpacing(0);

  QLabel *upgrade = new QLabel(tr("Upgrade Now"));
  upgrade->setStyleSheet("font-size: 75px; font-weight: bold;");
  main_layout->addWidget(upgrade, 0, Qt::AlignTop);
  main_layout->addSpacing(50);

  QLabel *description = new QLabel(tr("Become a comma prime member at connect.comma.ai"));
  description->setStyleSheet("font-size: 56px; font-weight: light; color: white;");
  description->setWordWrap(true);
  main_layout->addWidget(description, 0, Qt::AlignTop);

  main_layout->addStretch();

  QLabel *features = new QLabel(tr("PRIME FEATURES:"));
  features->setStyleSheet("font-size: 41px; font-weight: bold; color: #E5E5E5;");
  main_layout->addWidget(features, 0, Qt::AlignBottom);
  main_layout->addSpacing(30);

  QVector<QString> bullets = {tr("Remote access"), tr("24/7 LTE connectivity"), tr("1 year of drive storage"), tr("Remote snapshots")};
  for (auto &b : bullets) {
    const QString check = "<b><font color='#AAED70'>✓</font></b> ";
    QLabel *l = new QLabel(check + b);
    l->setAlignment(Qt::AlignLeft);
    l->setStyleSheet("font-size: 50px; margin-bottom: 15px;");
    main_layout->addWidget(l, 0, Qt::AlignBottom);
  }

  setStyleSheet(R"(
    PrimeAdWidget {
      border-radius: 25px;
      background: qlineargradient(
        x1: 0, y1: 0,
        x2: 1, y2: 1,
        stop: 0 #081912,
        stop: 0.5 #2a5633,
        stop: 1 #5caa6c
      );
      border: 9px solid rgba(57, 255, 20, 0.3);
    }
  )");
}


PrimeAccountTypeWidget::PrimeAccountTypeWidget(QWidget* parent) : QWidget(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(0, 0, 0, 0);
  main_layout->setSpacing(0);

  stack = new QStackedWidget;
  stack->addWidget(new PrimeDefaultWidget);
  stack->addWidget(new PrimeSubscribedWidget);
  main_layout->addWidget(stack);

  QObject::connect(uiState()->prime_state, &PrimeState::changed, [this]() {
    stack->setCurrentIndex(uiState()->prime_state->isSubscribed() ? 1 : 0);
  });

  setFixedHeight(244);
}


SetupWidget::SetupWidget(QWidget* parent) : QFrame(parent) {
  mainLayout = new QStackedWidget;
  setFixedHeight(650);

  // Unpaired, registration prompt layout

  QFrame* finishRegistration = new QFrame;
  finishRegistration->setObjectName("primeWidget");
  QVBoxLayout* finishRegistrationLayout = new QVBoxLayout(finishRegistration);
  finishRegistrationLayout->setSpacing(0);
  finishRegistrationLayout->setContentsMargins(40, 60, 40, 40);

  QLabel* registrationTitle = new QLabel(tr("Last step to finish setup!"));
  registrationTitle->setStyleSheet("font-size: 70px; font-weight: bold; color: #FFEB88; padding: 0 30px;");
  registrationTitle->setFixedHeight(180);
  registrationTitle->setWordWrap(true);
  finishRegistrationLayout->addWidget(registrationTitle);

  QLabel* registrationDescription = new QLabel(tr("Pair your device with comma connect and claim your comma prime offer."));
  registrationDescription->setWordWrap(true);
  registrationDescription->setStyleSheet("font-size: 40px; font-weight: normal; color: white; padding: 0 30px;");
  finishRegistrationLayout->addWidget(registrationDescription);

  QPushButton* pair = new QPushButton(tr("Pair device"));
  pair->setStyleSheet(R"(
    QPushButton {
      font-size: 50px;
      font-weight: 500;
      border-radius: 25px;
      background-color: #465BEA;
      padding: 33px;
    }
    QPushButton:pressed {
      background-color: #3049F4;
    }
  )");
  finishRegistrationLayout->addWidget(pair);

  popup = new PairingPopup(this);
  QObject::connect(pair, &QPushButton::clicked, popup, &PairingPopup::exec);

  mainLayout->addWidget(finishRegistration);

  // build stacked layout
  QVBoxLayout *outer_layout = new QVBoxLayout(this);
  outer_layout->setContentsMargins(0, 0, 0, 0);
  outer_layout->addWidget(mainLayout);

  QWidget *content = new QWidget;
  QVBoxLayout *content_layout = new QVBoxLayout(content);
  content_layout->setContentsMargins(0, 0, 0, 0);
  content_layout->setSpacing(30);

  WiFiPromptWidget *wifi_prompt = new WiFiPromptWidget;
  QObject::connect(wifi_prompt, &WiFiPromptWidget::openSettings, this, &SetupWidget::openSettings);
  content_layout->addWidget(wifi_prompt);
  content_layout->addStretch();

  mainLayout->addWidget(content);

  mainLayout->setCurrentIndex(1);

  setStyleSheet(R"(
    #primeWidget {
      border-radius: 25px;
      background-color: #333333;
    }
  )");

  // Retain size while hidden
  QSizePolicy sp_retain = sizePolicy();
  sp_retain.setRetainSizeWhenHidden(true);
  setSizePolicy(sp_retain);

  QObject::connect(uiState()->prime_state, &PrimeState::changed, [this](PrimeState::Type type) {
    if (type == PrimeState::PRIME_TYPE_UNPAIRED) {
      mainLayout->setCurrentIndex(0);  // Display "Pair your device" widget
    } else {
      popup->reject();
      mainLayout->setCurrentIndex(1);  // Display Wi-Fi prompt widget
    }
  });
}
