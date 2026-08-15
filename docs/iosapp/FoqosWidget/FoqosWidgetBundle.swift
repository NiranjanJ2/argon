//
//  FoqosWidgetBundle.swift
//  FoqosWidget
//
//  Created by Ali Waseem on 2025-03-11.
//

import SwiftUI
import WidgetKit

@main
struct FoqosWidgetBundle: WidgetBundle {
  var body: some Widget {
    // Argon's widget leads: on a Mac this sits in Notification Center, where
    // "what am I meant to be doing" is the only question worth answering.
    // ProfileControlWidget is foqos's profile launcher — kept, because starting
    // a block by hand is still occasionally what you want, but it is no longer
    // the first thing offered.
    ArgonTodayWidget()
    ProfileControlWidget()
    FoqosWidgetLiveActivity()
  }
}
