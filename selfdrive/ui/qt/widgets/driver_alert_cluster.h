#pragma once

#include <QWidget>
#include <QTimer>
#include <QIcon>
#include <array>
#include <QHash>
#include "selfdrive/ui/ui.h"

class DriverAlertCluster : public QWidget {
  Q_OBJECT

public:
  explicit DriverAlertCluster(UIState *ui_state, QWidget *parent = nullptr);
  ~DriverAlertCluster();

  void updateState(const UIState &s);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  static constexpr int NUM_ALERT_BARS = 3;
  static constexpr int NUM_ALERT_LEVELS = 8;
  static constexpr int NUM_CIRCLES = 7;

  struct AlertBar {
    QString label;
    int alertLevel;
    QString iconName;
  };

  struct AlertProperties {
    QColor borderColor;
    QColor fillColor;
    QColor iconColor;
    QColor textColor;
    int borderWidth;

    // REMOVE - Shadow code
    // QColor shadowColor;
    // qreal shadowOpacity;
    // qreal shadowBlurRadius;
    // qreal shadowSpread;

    std::array<QColor, NUM_CIRCLES> circleColors;
  };

  struct IconInfo {
    QIcon icon;
    qreal aspectRatio;
  };

  // UI Constants
  static constexpr int BAR_WIDTH = 585; // Individual bar widths
  static constexpr int BAR_HEIGHT = 87; // Individual bar heights
  static constexpr int HORIZONTAL_PADDING = 20; // Controls horizontal space of the whole DAC UI from left edge of screen
  static constexpr int VERTICAL_PADDING = 20; // Controls vertical spacing between the DAC bars
  static constexpr int CIRCLE_SIZE = 33;
  static constexpr int FONT_SIZE = 13;
  static constexpr int ICON_SIZE = 50;
  static constexpr int CORNER_RADIUS = 41;
  static constexpr int CIRCLE_AREA_WIDTH = 310; // How close together circles are
  static constexpr int ICON_PADDING = 30; // Left side padding?
  static constexpr int CIRCLE_RIGHT_MARGIN = 20; // Controls spacing of the right side of the individual bar
  static constexpr int TEXT_ICON_SPACING = 13;

  void initializeAlertBars();
  bool loadIcons();
  cereal::ModelDataV2::DisengagePredictions::Reader getDisengagePredictions(const SubMaster &sm) const;
  int calculateAlertLevel(const capnp::List<float>::Reader& probs);
  AlertProperties getAlertProperties(int alertLevel);
  void drawAlertBar(QPainter &painter, const AlertBar &alertBar, int yOffset);
  bool renderIcon(QPainter &painter, const QString &iconName, const QRectF &rect, const QColor &color);

  // REMOVE - Shadow code
  // void drawGradientShadow(QPainter &painter, const QRectF &rect, const AlertProperties &properties);

  void drawRoundedRect(QPainter &painter, const QRectF &rect, qreal xRadius, qreal yRadius);

  std::array<AlertBar, NUM_ALERT_BARS> alertBars;
  QHash<QString, IconInfo> iconInfo;
  UIState *ui_state;
  QTimer *updateTimer;

  // Cached properties for performance
  std::array<AlertProperties, NUM_ALERT_LEVELS> cachedAlertProperties;
};
